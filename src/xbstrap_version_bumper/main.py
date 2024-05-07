import os
import pathlib
from functools import cmp_to_key
from typing import Any
import hashlib
import argparse

import git
import libversion
import ruamel.yaml
import requests
from git import Repo, Actor
from pprint import pprint
from git import RemoteProgress

import xbstrap_version_bumper.linecounted_yaml

global_yaml = ruamel.yaml.YAML(typ='safe')
global_yaml.Constructor = xbstrap_version_bumper.linecounted_yaml.MyConstructor


def sort_versions(version_list: list) -> str:
    return sorted(version_list, key=cmp_to_key(libversion.version_compare2))[-1]


class ProgressPrinter(RemoteProgress):
    def update(self, op_code, cur_count, max_count=None, message=""):
        print(
            op_code,
            cur_count,
            max_count,
            cur_count / (max_count or 100.0),
            message or "NO MESSAGE",
        )


class Change:
    def __init__(self, line: int, base_str: str = "", to_str: str = "",
                 delete_line: bool = False, insert_line: bool = False, insert_auto_indent: bool = True,
                 update_is_version=False):
        self.line = line
        self.base_str = base_str
        self.to_str = to_str
        self.delete_line = delete_line
        self.insert_line = insert_line
        self.insert_auto_indent = insert_auto_indent
        self.update_is_version = update_is_version

    def apply(self, line):
        if not self.update_is_version:
            return line.replace(self.base_str, self.to_str)

        # This is a version, there are several ways these are commonly encoded in tags,
        # try to support all of them
        current_line = line
        if self.base_str.replace('.', '_') in line:
            current_line = current_line.replace(self.base_str.replace('.', '_'), self.to_str.replace('.', '_'))
        if self.base_str.replace('.', '-') in line:
            current_line = current_line.replace(self.base_str.replace('.', '-'), self.to_str.replace('.', '-'))
        if self.base_str in line:
            current_line = current_line.replace(self.base_str, self.to_str)

        return current_line


class StrapFile:
    def __init__(self, path):
        self.path = path
        self.yaml = global_yaml.load(pathlib.Path(path))
        self.changes = []

        # pprint(self.yaml)
        self.imports = []
        if 'imports' in self.yaml:
            for file_import in self.yaml['imports']:
                self.imports.append(file_import['file'])

    def emit(self):
        #global_yaml.dump(self.yaml, pathlib.Path(self.path))
        #global_yaml.dump(self.yaml, sys.stdout)
        if len(self.changes) == 0:
            return

        print(f'---> Emitting changes to {self.path}')

        lines = []
        with open(self.path, 'r') as f:
            for line in f:
                lines.append(line)

        deleted_lines = []
        inserted_lines = {}

        for change in self.changes:
            if change.delete_line:
                print(f'----> "{lines[change.line].strip()}" -> line removed')
                deleted_lines.append(change.line)
                continue
            elif change.insert_line:
                print(f'----> "{change.base_str.strip()}" -> line inserted')
                inserted_lines[change.line] = change
                continue

            new_line = change.apply(lines[change.line])
            print(f'----> "{lines[change.line].strip()}" -> "{new_line.strip()}"')
            lines[change.line] = new_line

        self.changes.clear()
        with open(self.path, 'w') as f:
            for i, line in enumerate(lines):
                if i in inserted_lines.keys():
                    leading_spaces = 0
                    # If we need to calculate the autoindent for this, base it of the line below the new one
                    if inserted_lines[i].insert_auto_indent:
                        leading_spaces = len(line) - len(line.lstrip())
                    leading_spaces_str = ' ' * leading_spaces
                    line_to_write = leading_spaces_str + inserted_lines[i].base_str + '\n'
                    f.write(line_to_write)
                if i not in deleted_lines:
                    f.write(line)


class Distro:
    def __init__(self, dir):
        self.strapfiles = []
        self.modified_files = {}
        self.dir = dir
        self.add_strap_file('bootstrap.yml')

    def add_strap_file(self, file):
        new_strapfile = StrapFile(os.path.join(self.dir, file))
        self.strapfiles.append(new_strapfile)

        for file_imports in new_strapfile.imports:
            self.add_strap_file(file_imports)

    def __locate_source(self, source_name) -> tuple[Any, bool, dict]:
        for strapfile in self.strapfiles:
            # Check all of the sources
            if 'sources' in strapfile.yaml:
                for source in strapfile.yaml['sources']:
                    if source['name'] == source_name:
                        return strapfile, False, source

            # Now check all of the packages
            if 'packages' in strapfile.yaml:
                for package in strapfile.yaml['packages']:
                    if 'source' in package and package['name'] == source_name:
                        return strapfile, True, package['source']

        return False, False

    def __locate_package(self, package_name) -> tuple[Any, Any] | tuple[bool, bool]:
        for strapfile in self.strapfiles:
            if 'packages' in strapfile.yaml:
                for package in strapfile.yaml['packages']:
                    if 'source' in package and package['name'] == package_name:
                        return strapfile, package

        return False, False

    def __add_changes_to_stapfile(self, strapfile: StrapFile, changes: []):
        for i, actual_strapfile in enumerate(self.strapfiles):
            if actual_strapfile.path != strapfile.path:
                continue

            self.strapfiles[i].changes += changes
            self.modified_files[strapfile.path] = True

    def __modify_source_impl(self, source, strapfile: StrapFile, source_name, new_version):
        changes = []

        old_version = source['version']
        print(f'--> Changing version of {source_name} from {old_version} to {new_version}')
        changes.append(Change(source['version'].lc.line, old_version, new_version))

        if 'url' in source:
            url_change = Change(source['url'].lc.line, old_version, new_version, update_is_version=True)
            new_url = url_change.apply(source['url'])
            changes.append(url_change)

        if 'checksum' in source:
            assert 'url' in source
            print(f'---> Attempting to download "{new_url}" to make new checksum')
            r = requests.get(new_url)
            new_checksum = hashlib.blake2b(r.content).hexdigest()
            changes.append(Change(source['checksum'].lc.line, str(source['checksum']), f'blake2b:{new_checksum}'))
            print(f'---> Updated checksum to blake2b:{new_checksum}')

        if 'filename' in source:
            changes.append(Change(source['filename'].lc.line, old_version, new_version, update_is_version=True))

        if 'tag' in source:
            changes.append(Change(source['tag'].lc.line, old_version, new_version, update_is_version=True))

        if 'extract_path' in source:
            changes.append(Change(source['extract_path'].lc.line, old_version, new_version, update_is_version=True))

        self.__add_changes_to_stapfile(strapfile, changes)

    def modify_source_version(self, source_name, new_version):
        # Locate the source
        strapfile, source_in_package, source = self.__locate_source(source_name)
        self.__modify_source_impl(source, strapfile, source_name, new_version)

    def __args_to_changes(self, args, from_str, to_str, update_is_version=False):
        changes = []
        for arg in args:
            if from_str in arg:
                changes.append(Change(arg.lc.line, from_str, to_str, update_is_version=update_is_version))

        return changes

    def modify_package_version(self, package_name, new_version):
        strapfile, package = self.__locate_package(package_name)
        changes = []
        if 'source' in package:
            old_version = str(package['source']['version'])
            self.__modify_source_impl(package['source'], strapfile, package_name, new_version)
        else:
            assert 'from_source' in package
            strapfile, source_in_package, source = self.__locate_source(package['from_source'])
            old_version = str(source['version'])
            self.__modify_source_impl(source, strapfile, package['from_source'], new_version)

        if 'revision' in package:
            changes.append(Change(package['revision'].lc.line,
                                  f'revision: {package['revision']}',
                                  'revision: 1'))
        else:
            if 'configure' in package:
                revision_insert_line_num = package['configure'].lc.line - 1
            elif 'build' in package:
                revision_insert_line_num = package['build'].lc.line - 1
            else:
                raise Exception('Could not find a suitable place to insert revision tag')
            changes.append(Change(revision_insert_line_num, 'revision: 1', insert_line=True))

        if 'configure' in package:
            for configure in package['configure']:
                changes += self.__args_to_changes(configure['args'], old_version, new_version, update_is_version=True)

        if 'build' in package:
            for build in package['build']:
                changes += self.__args_to_changes(build['args'], old_version, new_version, update_is_version=True)

        self.__add_changes_to_stapfile(strapfile, changes)

    def __get_latest_git_version(self, source):
        if 'git' not in source:
            return False
        g = git.cmd.Git()
        blob = g.ls_remote(source['git'], sort='-v:refname', tags=True)
        available_versions = []
        for line in blob.split('\n'):
            available_versions.append(line.split('/')[-1])
        newest_version = sort_versions(available_versions)
        print(f'---> Latest git tag: {newest_version}')

        if newest_version[0] == 'v':
            newest_version = newest_version[1:]
        print(f'---> Latest version: {newest_version}')

        return newest_version

    def get_latest_git_version_of_package(self, package_name):
        strapfile, package = self.__locate_package(package_name)
        if 'source' in package:
            return self.__get_latest_git_version(package['source'])
        else:
            assert 'from_source' in package
            strapfile, source_in_package, source = self.__locate_source(package['from_source'])
            return self.__get_latest_git_version(source)

    def get_latest_git_version_of_source(self, source_name):
        strapfile, source_in_package, source = self.__locate_source(source_name)
        return self.__get_latest_git_version(source)

    def emit_modified_yaml(self):
        for strapfile in self.strapfiles:
            if strapfile.path not in self.modified_files.keys():
                continue

            strapfile.emit()

        self.modified_files.clear()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('to_modify')
    parser.add_argument('--is-source', action='store_true')
    parser.add_argument('--set-version')
    parser.add_argument('--bootstrap-dir', required=True)
    parser.add_argument('--pull-from-master', action='store_true')
    parser.add_argument('--create-branch', action='store_true')
    parser.add_argument('--commiter-name')
    parser.add_argument('--commiter-email')
    parser.add_argument('--attempt-latest-version-autodetect', action='store_true')
    parser.add_argument('--try-global-update', action='store_true')
    args = parser.parse_args()

    pprint(args)

    if not args.try_global_update and args.to_modify is None:
        parser.error('to_modify is needed if --try-global-update is not given')

    if args.try_global_update and not args.attempt_latest_version_autodetect:
        parser.error('--try-global-update requires --attempt-latest-version-autodetect')

    if not (args.set_version or args.attempt_latest_version_autodetect):
        parser.error('--set-version or --attempt-latest-version-autodetect is required')

    if args.create_branch and (args.commiter_name is None or args.commiter_email is None):
        parser.error('--commiter-name and --commiter-email is required when creating a branch')

    repo = Repo(args.bootstrap_dir)
    assert not repo.bare

    if args.pull_from_master:
        for remote in repo.remotes:
            if remote.name == 'origin':
                # Pull master from origin
                print(f'--> Pulling from origin ({remote.url})')
                remote.pull(progress=ProgressPrinter())

    print('-> Reading bootstrap files')
    distro = Distro(args.bootstrap_dir)

    version_to_set = args.set_version
    if args.attempt_latest_version_autodetect:
        if not args.is_source:
            attempt_version_find = distro.get_latest_git_version_of_package(args.to_modify)
        else:
            attempt_version_find = distro.get_latest_git_version_of_source(args.to_modify)
        if attempt_version_find:
            version_to_set = attempt_version_find

    if version_to_set is None:
        print('----> Failed to determine a version to set!')

    if args.is_source:
        if version_to_set is not None:
            distro.modify_source_version(args.to_modify, version_to_set)
    else:
        if version_to_set is not None:
            distro.modify_package_version(args.to_modify, version_to_set)

    if args.create_branch:
        print('-> Stashing current git changes and checking out new branch from master')
        repo.git.stash('save')
        repo.git.checkout('master')
        repo.git.checkout('-b', f'xbstrap_version_bumper_{args.to_modify}_to_{args.set_version}')

    distro.emit_modified_yaml()

    if args.create_branch:
        print('-> Finalizing git changes')
        print('--> Adding all changes')
        repo.git.add(all=True)
        main_author = Actor(args.commiter_name, args.commiter_email)
        print('--> Commiting to new branch')
        repo.index.commit(f'{args.to_modify}: update to {args.set_version}', author=main_author)
        print('--> Returning to master')
        repo.git.checkout('master')

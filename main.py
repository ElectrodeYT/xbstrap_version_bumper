import os
import pathlib
import sys
from typing import Tuple, Any
import hashlib
import argparse

import ruamel.yaml
import requests
from git import Repo, Actor
from pprint import pprint
from git import RemoteProgress

import linecounted_yaml

global_yaml = ruamel.yaml.YAML(typ='rt')
global_yaml.indent(sequence=4, offset=2)
global_yaml.preserve_quotes = True
global_yaml.Constructor = linecounted_yaml.MyConstructor


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
    def __init__(self, line: int, from_str: str = "", to_str: str = "", delete_line: bool = False):
        self.line = line
        self.from_str = from_str
        self.to_str = to_str
        self.delete_line = delete_line

    def apply(self, line):
        return line.replace(self.from_str, self.to_str)


class StrapFile:
    def __init__(self, path):
        print(f'--> Reading bootstrap file "{path}"')
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

        for change in self.changes:
            if change.delete_line:
                print(f'----> "{lines[change.line].strip()}" -> line removed')
                deleted_lines.append(change.line)
                continue

            new_line = change.apply(lines[change.line])
            print(f'----> "{lines[change.line].strip()}" -> "{new_line.strip()}"')
            lines[change.line] = new_line

        self.changes.clear()
        with open(self.path, 'w') as f:
            for i, line in enumerate(lines):
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

        # Update the URL
        new_url = source['url'].replace(old_version, new_version)
        changes.append(Change(source['url'].lc.line, str(source['url']), new_url))

        if 'checksum' in source:
            print(f'---> Attempting to download "{new_url}" to make new checksum')
            r = requests.get(new_url)
            new_checksum = hashlib.blake2b(r.content).hexdigest()
            changes.append(Change(source['checksum'].lc.line, str(source['checksum']), f'blake2b:{new_checksum}'))
            print(f'---> Updated checksum to blake2b:{new_checksum}')

        if 'revision' in source:
            print('--> Deleting revision')
            source['revision'] = None

        if 'filename' in source:
            new_filename = source['filename'].replace(old_version, new_version)
            changes.append(Change(source['filename'].lc.line, str(source['filename']), new_filename))

        # self.__update_source(strapfile, source_in_package, source, source_name)
        self.__add_changes_to_stapfile(strapfile, changes)

    def modify_source_version(self, source_name, new_version):
        # Locate the source
        strapfile, source_in_package, source = self.__locate_source(source_name)
        self.__modify_source_impl(source, strapfile, source_name, new_version)

    def __args_to_changes(self, args, from_str, to_str):
        changes = []
        for arg in args:
            if from_str in arg:
                changes.append(Change(arg.lc.line, from_str, to_str))

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
            changes.append(Change(package['revision'].lc.line, delete_line=True))

        if 'configure' in package:
            for configure in package['configure']:
                changes += self.__args_to_changes(configure['args'], old_version, new_version)

        if 'build' in package:
            for build in package['build']:
                changes += self.__args_to_changes(build['args'], old_version, new_version)

        self.__add_changes_to_stapfile(strapfile, changes)

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
    parser.add_argument('--bootstrap-dir', default='bootstrap-managarm')
    parser.add_argument('--create-branch', action='store_true')
    parser.add_argument('--commiter-name')
    parser.add_argument('--commiter-email')
    args = parser.parse_args()

    pprint(args)

    if not (args.set_version):
        parser.error('--set-version is required')

    if args.create_branch and (args.commiter_name is None or args.commiter_email is None):
        parser.error('--commiter-name and --commiter-email is required when creating a branch')

    print("-> Ensuring local master is up to date")
    repo = Repo(args.bootstrap_dir)
    assert not repo.bare

    for remote in repo.remotes:
        if remote.name == 'origin':
            # Pull master from origin
            print(f'--> Pulling from origin ({remote.url})')
            #remote.pull(progress=ProgressPrinter())

    print('-> Reading bootstrap files')
    distro = Distro('bootstrap-managarm')

    if args.is_source:
        if args.set_version is not None:
            distro.modify_source_version(args.to_modify, args.set_version)
    else:
        if args.set_version is not None:
            distro.modify_package_version(args.to_modify, args.set_version)

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


if __name__ == '__main__':
    main()

import os
import pathlib
import sys
from typing import Tuple, Any
import hashlib

import ruamel.yaml
import requests
from git import Repo
from pprint import pprint
from git import RemoteProgress

global_yaml = ruamel.yaml.YAML(typ='rt')
global_yaml.indent(sequence=4, offset=2)
global_yaml.preserve_quotes = True


class ProgressPrinter(RemoteProgress):
    def update(self, op_code, cur_count, max_count=None, message=""):
        print(
            op_code,
            cur_count,
            max_count,
            cur_count / (max_count or 100.0),
            message or "NO MESSAGE",
        )


class StrapFile:
    def __init__(self, path):
        print(f'--> Reading bootstrap file "{path}"')
        self.path = path
        self.yaml = global_yaml.load(pathlib.Path(path))

        # pprint(self.yaml)
        self.imports = []
        if 'imports' in self.yaml:
            for file_import in self.yaml['imports']:
                self.imports.append(file_import['file'])

    def emit(self):
        global_yaml.dump(self.yaml, pathlib.Path(self.path))
        #global_yaml.dump(self.yaml, sys.stdout)


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

    def __update_source(self, strapfile, source_in_package, source, source_name):
        self.modified_files[strapfile.path] = True
        for i, actual_strapfile in enumerate(self.strapfiles):
            if actual_strapfile.path != strapfile.path:
                continue

            if source_in_package:
                assert 'packages' in actual_strapfile.yaml
                for x, package in enumerate(actual_strapfile.yaml['packages']):
                    if package['name'] == source_name:
                        self.strapfiles[i].yaml['packages'][x]['source'] = source
                        return
            else:
                assert 'sources' in actual_strapfile.yaml
                for x, source in enumerate(actual_strapfile.yaml['sources']):
                    if source['name'] == source_name:
                        self.strapfiles[i].yaml['sources'][x] = source
                        return

        assert False

    def modify_source_version(self, source_name, new_version):
        # Locate the source
        strapfile, source_in_package, source = self.__locate_source(source_name)
        pprint(source)

        old_version = source['version']
        print(f'--> Updating version of {source_name} from {old_version} to {new_version}')

        source['version'] = new_version
        source['url'] = source['url'].replace(old_version, new_version)

        if 'checksum' in source:
            print(f'---> Attempting to download "{source['url']}" to make new checksum')
            r = requests.get(source['url'])
            new_checksum = hashlib.blake2b(r.content).hexdigest()
            source['checksum'] = f'blake2b:{new_checksum}'
            print(f'---> Updated checksum to blake2b:{new_checksum}')

        if 'revision' in source:
            print('--> Deleting revision')
            source['revision'] = None

        self.__update_source(strapfile, source_in_package, source, source_name)

    def emit_modified_yaml(self):
        for strapfile in self.strapfiles:
            if strapfile.path not in self.modified_files.keys():
                continue

            strapfile.emit()

        self.modified_files.clear()

def main():
    print("-> Ensuring local master is up to date")
    repo = Repo('bootstrap-managarm')
    assert not repo.bare

    for remote in repo.remotes:
        if remote.name == 'origin':
            # Pull master from origin
            print(f'--> Pulling from origin ({remote.url})')
            #remote.pull(progress=ProgressPrinter())

    print('-> Reading bootstrap files')
    distro = Distro('bootstrap-managarm')

    package_name = 'mednafen'
    new_version = '1.23.0'
    distro.modify_source_version(package_name, new_version)
    distro.emit_modified_yaml()


if __name__ == '__main__':
    main()

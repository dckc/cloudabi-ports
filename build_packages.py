#!/usr/bin/env python3
# Copyright (c) 2015 Nuxi, https://nuxi.nl/
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE file for details.

import logging
from random import Random

from src import config
from src import util
from src.catalog import (ArchLinuxCatalog, CygwinCatalog, DebianCatalog,
                         FreeBSDCatalog, HomebrewCatalog, NetBSDCatalog,
                         OpenBSDCatalog, RedHatCatalog)
from src.distfile import IO
from src.repository import Repository
from src.version import FullVersion

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Locations relative to the source tree.
DIR_ROOT = '.'
DIR_DISTFILES = '_obj/distfiles'
DIR_INSTALL = '_obj/install'
DIR_PACKAGES_ARCHLINUX = '_obj/packages/archlinux'
DIR_PACKAGES_CYGWIN = '_obj/packages/cygwin'
DIR_PACKAGES_DEBIAN = '_obj/packages/debian'
DIR_PACKAGES_FREEBSD = '_obj/packages/freebsd'
DIR_PACKAGES_HOMEBREW = '_obj/packages/homebrew'
DIR_PACKAGES_NETBSD = '_obj/packages/netbsd'
DIR_PACKAGES_OPENBSD = '_obj/packages/openbsd'
DIR_PACKAGES_REDHAT = '_obj/packages/redhat'
DIR_REPOSITORY = 'packages'

catalogs = {
    ArchLinuxCatalog(None, DIR_PACKAGES_ARCHLINUX),
    CygwinCatalog(None, DIR_PACKAGES_CYGWIN),
    DebianCatalog(None, DIR_PACKAGES_DEBIAN),
    FreeBSDCatalog(None, DIR_PACKAGES_FREEBSD),
    HomebrewCatalog(None, DIR_PACKAGES_HOMEBREW, 'http://example.com/'),
    NetBSDCatalog(None, DIR_PACKAGES_NETBSD),
    OpenBSDCatalog(None, DIR_PACKAGES_OPENBSD),
    RedHatCatalog(None, DIR_PACKAGES_REDHAT),
}


def main(argv, cwd, subprocess, urlopen):
    # Parse all of the BUILD rules.
    rng = Random(x=1)
    io = IO(urlopen, subprocess, rng)
    repo = Repository(cwd / DIR_INSTALL, io)
    for file in util.walk_files(cwd / DIR_REPOSITORY):
        if file.name == 'BUILD':
            repo.add_build_file(file, cwd / DIR_DISTFILES)
    target_packages = repo.get_target_packages()

    if len(argv) > 1:
        # Only build the packages provided on the command line.
        for name in set(argv[1:]):
            for arch in config.ARCHITECTURES:
                build_package(target_packages[(name, arch)])
    else:
        # Build all packages.
        for package in target_packages.values():
            build_package(package)

    # When terminating successfully, remove the build directory. It will
    # only contain temporary files that were used to generate the last
    # package.
    util.remove(config.DIR_BUILDROOT)


def build_package(package):
    version = FullVersion(version=package.get_version())
    for catalog in catalogs:
        catalog.insert(package, version, catalog.package(package, version))


if __name__ == '__main__':
    def _script():
        from os import link as os_link
        from pathlib import PosixPath
        from sys import argv
        from urllib.request import urlopen
        import shutil
        import subprocess

        shutil_path = util.mix_shutil_path(PosixPath, shutil, os_link)
        cwd = shutil_path('.')
        main(argv[:], cwd, subprocess, urlopen)

    _script()

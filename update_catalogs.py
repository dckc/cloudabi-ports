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
from src.catalog_set import CatalogSet
from src.distfile import IO
from src.repository import Repository

# Setup logging
logging.basicConfig(level=logging.INFO)

# Public location where package distfiles are stored.
DIR_DISTFILES = '/usr/local/www/nuxi.nl/public/distfiles/third_party'

# Temporary directory where intermediate build results are stored.
DIR_TMP = '/usr/local/www/nuxi.nl/repo.tmp'

# Final location of the catalogs.
DIR_CATALOG_PARENT = '/usr/local/www/nuxi.nl/public/distfiles/cloudabi-ports/'

# Location of the catalog signing keys.
ARCHLINUX_PRIVATE_KEY = '31344B15'
CYGWIN_PRIVATE_KEY = 'A4836F43'
DEBIAN_PRIVATE_KEY = '31344B15'
FREEBSD_PRIVATE_KEY = '/home/edje/.cloudabi-ports-freebsd.key'
REDHAT_PRIVATE_KEY = '31344B15'

# The Homebrew repository needs to know its own URL.
HOMEBREW_URL = 'https://nuxi.nl/distfiles/cloudabi-ports/homebrew/'


class OS:
    [archlinux, cygwin, debian,
     freebsd, homebrew, netbsd,
     openbsd, redhat] = (
         'archlinux cygwin debian '
         'freebsd homebrew netbsd '
         'openbsd redhat').split()


def main(argv, cwd):
    # Zap the old temporary directory.
    util.remove_and_make_dir(cwd / DIR_TMP)

    # Parse all of the BUILD rules.
    rng = Random(x=1)
    io = IO(urlopen=None, subprocess=None, random=rng)
    repo = Repository(cwd / DIR_TMP / 'install', io)
    # repo = Repository(os.path.join(os.getcwd(), '_obj/install'))
    for file in util.walk_files(cwd / 'packages'):
        if file.name == 'BUILD':
            repo.add_build_file(file, cwd / DIR_DISTFILES)

    target_packages = repo.get_target_packages()

    published = cwd / DIR_CATALOG_PARENT
    workspace = cwd / DIR_TMP

    # The catalogs that we want to create.
    archlinux_catalog = ArchLinuxCatalog(published / OS.archlinux,
                                         workspace / OS.archlinux)
    cygwin_catalog = CygwinCatalog(published / OS.cygwin,
                                   workspace / OS.cygwin)
    debian_catalog = DebianCatalog(published / OS.debian,
                                   workspace / OS.debian)
    freebsd_catalog = FreeBSDCatalog(published / OS.freebsd,
                                     workspace / OS.freebsd)
    homebrew_catalog = HomebrewCatalog(published / OS.homebrew,
                                       workspace / OS.homebrew,
                                       HOMEBREW_URL)
    netbsd_catalog = NetBSDCatalog(published / OS.netbsd,
                                   workspace / OS.netbsd)
    openbsd_catalog = OpenBSDCatalog(published / OS.openbsd,
                                     workspace / OS.openbsd)
    redhat_catalog = RedHatCatalog(published / OS.redhat,
                                   workspace / OS.redhat)

    # Build all packages.
    catalog_set = CatalogSet({
        archlinux_catalog, cygwin_catalog, debian_catalog, freebsd_catalog,
        homebrew_catalog, netbsd_catalog, openbsd_catalog, redhat_catalog,
    })
    for package in target_packages.values():
        catalog_set.package_and_insert(package, cwd / DIR_TMP / 'catalog')

    archlinux_catalog.finish(ARCHLINUX_PRIVATE_KEY)
    cygwin_catalog.finish(CYGWIN_PRIVATE_KEY)
    debian_catalog.finish(DEBIAN_PRIVATE_KEY)
    freebsd_catalog.finish(FREEBSD_PRIVATE_KEY)
    redhat_catalog.finish(REDHAT_PRIVATE_KEY)

    # Finish up and put the new catalogs in place.
    for os_name in OS.names:
        pub = published / os_name
        work = workspace / os_name
        pub.rename(work.with_name(os_name + '.old'))
        work.rename(pub)

    # Zap the temporary directories.
    util.remove(cwd / config.DIR_BUILDROOT)
    util.remove(cwd / DIR_TMP)


if __name__ == '__main__':
    def _script():
        from os import link as os_link
        from pathlib import PosixPath
        from sys import argv
        import shutil

        shutil_path = util.mix_shutil_path(PosixPath, shutil, os_link)
        cwd = shutil_path('.')
        main(argv[:], cwd)

    _script()

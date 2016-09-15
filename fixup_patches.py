#!/usr/bin/env python3
# Copyright (c) 2015 Nuxi, https://nuxi.nl/
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE file for details.

import logging
from random import Random

from src import util
from src.distfile import IO
from src.repository import Repository

# Setup logging
logging.basicConfig(level=logging.INFO)

# Locations relative to the source tree.
DIR_DISTFILES = '_obj/distfiles'
DIR_TMP = '_obj/fixup_patches'


def main(argv, cwd, subprocess, urlopen):
    '''
    @param argv: traditional command line arguments (strings)
    @param cwd: equivalent to an fd for the current working directory,
                with a bit of a kludge: we can navigate to absolute paths
    @param subprocess: equivalent to authority to open all files in $PATH
                       (and hence, to cloudabi_sys_proc_exec them)
                       with a big kludge: subprocesses can access arbitrary
                       files.
                       TODO: fashion an API to simulate passing paths
                       TODO: attenuate this to capabilities to run
                       specific subprocesses
    @param urlopen: equivalent to an fd to a service that can open
                    TCP connections.
                    TODO: attenuate to specific URLs
    '''
    # Parse all of the BUILD rules.
    rng = Random(x=1)
    io = IO(urlopen, subprocess, rng)
    repo = Repository(None, io)
    for file in util.walk_files(cwd / argv[1]):
        if file.name == 'BUILD':
            repo.add_build_file(file, cwd / DIR_DISTFILES)

    # Regenerate all the patches.
    for distfile in repo.get_distfiles():
        distfile.fixup_patches(cwd / DIR_TMP)


if __name__ == '__main__':
    def _script():
        from pathlib import PosixPath
        from sys import argv
        import shutil
        import subprocess
        from urllib.request import urlopen

        def os_link(src, dst):
            raise IOError('not allowed')

        shutil_path = util.mix_shutil_path(PosixPath, shutil, os_link)
        cwd = shutil_path('.')
        main(argv[:], cwd, subprocess, urlopen)

    _script()

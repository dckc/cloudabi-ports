# Copyright (c) 2015-2016 Nuxi, https://nuxi.nl/
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE file for details.

from pathlib import PurePath
from pkgutil import get_data
from random import Random as RandomT
from typing import Callable, NamedTuple
import string

# Architectures for which we can build packages.
ARCHITECTURES = {
    'aarch64-unknown-cloudabi',
    'armv6-unknown-cloudabi-eabihf',
    'i686-unknown-cloudabi',
    'x86_64-unknown-cloudabi',
}

# Resource files.
DIR_RESOURCES = PurePath(__file__).parent / 'misc'
RESOURCES = dict((name, get_data('misc', name))
                 for name in ['config.guess', 'config.sub'])

# Location at which distfiles can be fetched in case the master sites
# are down.
FALLBACK_MIRRORS = {'https://nuxi.nl/distfiles/third_party/'}


# Temporary directory where packages will be built. This directory has
# to be fixed, as the compilation process tends to hardcode paths to the
# build directory. Debug symbols and __FILE__ use absolute paths.
DIR_BUILDROOT = object()

# Host C and C++ compiler, used to compile the build tools. We'd better
# use Clang if available. Compared to GCC, it has the advantage that it
# does not depend on the 'as' and 'ld' utilities being part of $PATH.
[HOST_CC, HOST_CXX] = [object(), object()]

# Name of the Perl executable.
PERL = object()

RANDOM = object()
USR = object()
USR_LOCAL = object()

Default = {
    USR: '/usr',
    USR_LOCAL: '/usr/local',
    DIR_BUILDROOT: '/usr/obj/cloudabi-ports',
    HOST_CC: '/usr/bin/cc',
    HOST_CXX: '/usr/bin/cc++',
    PERL: '/usr/bin/perl'}

Linux = {
    HOST_CC: '/usr/bin/clang-3.7',
    HOST_CXX: '/usr/bin/clang++-3.7'}

FreeBSD = {
    PERL: '/usr/local/bin/perl'}



def config_path_fn(system: str,
                   random: RandomT) -> Callable[[object], str]:
    roots = Default.copy()
    if system == 'Linux':
        roots.update(Linux)
    elif system == 'FreeBSD':
        roots.update(FreeBSD)
    def config_path(key):
        if key == RANDOM:
            return ''.join(
                random.choice(string.ascii_letters) for i in range(16))
        return roots[key]

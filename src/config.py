# Copyright (c) 2015-2016 Nuxi, https://nuxi.nl/
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE file for details.

from pkgutil import get_data

# Architectures for which we can build packages.
ARCHITECTURES = {
    'aarch64-unknown-cloudabi',
    'armv6-unknown-cloudabi-eabihf',
    'i686-unknown-cloudabi',
    'x86_64-unknown-cloudabi',
}

# Temporary directory where packages will be built. This directory has
# to be fixed, as the compilation process tends to hardcode paths to the
# build directory. Debug symbols and __FILE__ use absolute paths.
DIR_BUILDROOT = '/usr/obj/cloudabi-ports'


RESOURCES = dict((name, get_data('misc', name))
                 for name in ['config.guess', 'config.sub'])

# Location at which distfiles can be fetched in case the master sites
# are down.
FALLBACK_MIRRORS = {'https://nuxi.nl/distfiles/third_party/'}


class Platform(object):
    '''Host C and C++ compiler, used to compile the build tools.

    We'd better use Clang if available. Compared to GCC, it has the
    advantage that it does not depend on the 'as' and 'ld' utilities
    being part of $PATH.

    '''
    def __init__(self, system):
        self._system = system

    @property
    def host_cc(self):
        return ('/usr/bin/clang-3.7' if self._system == 'Linux' else
                '/usr/bin/cc')

    @property
    def host_cxx(self):
        return ('/usr/bin/clang++-3.7' if self._system == 'Linux' else
                '/usr/bin/c++')

    @property
    def perl(self):
        '''Name of the Perl executable.
        '''
        return ('/usr/local/bin/perl' if self._system == 'FreeBSD' else
                '/usr/bin/perl')

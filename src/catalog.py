# Copyright (c) 2015-2016 Nuxi, https://nuxi.nl/
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE file for details.

from subprocess import PIPE
from time import strftime, struct_time
from typing import Callable, Optional, Set, Tuple
import base64
import collections
import hashlib
import logging
import lzma
import math
import re
import stat

from . import config
from . import rpm
from . import util
from .package import TargetPackage as TPkg
from .util import PathExt
from .version import FullVersion

log = logging.getLogger(__name__)

_ClockFn = Callable[[], struct_time]


class Catalog:

    def __init__(self, old_path: PathExt, new_path: PathExt,
                 subprocess: util.RunCommand) -> None:
        self._old_path = old_path
        self._new_path = new_path
        self._packages = set()  # type: Set[Tuple[str, FullVersion]]
        self._subprocess = subprocess

    @staticmethod
    def _get_suggested_mode(path: PathExt) -> int:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            # Symbolic links.
            return 0o777
        elif stat.S_ISDIR(mode) or (mode & 0o111) != 0:
            # Directories and executable files.
            return 0o555
        else:
            # Non-executable files.
            return 0o444

    @staticmethod
    def _sanitize_permissions(directory: PathExt, directory_mode=0o555) -> None:
        for root, dirs, files in util.walk(directory):
            util.lchmod(root, directory_mode)
            for filename in files:
                path = root / filename
                util.lchmod(path, Catalog._get_suggested_mode(path))

    def _run_tar(self, args: List[str]) -> None:
        subprocess = self._subprocess
        platform = self._old_path.platform()
        subprocess.check_call([
            str(platform(config.DIR_BUILDROOT) / 'bin/bsdtar')
        ] + args)

    def insert(self, package, version, source) -> None:
        target = (
            self._new_path / self._get_filename(package, version))
        util.make_dir(self._new_path)
        util.remove(target)
        source.link(target)
        self._packages.add((package, version))

    def lookup_at_version(self, package: TPkg, version: FullVersion) -> Optional[PathExt]:
        if self._old_path:
            path = (
                self._old_path).pathjoin(
                self._get_filename(package, version))
            if path.exists():
                return path
        return None

    def _get_filename(self, package: TPkg, version: FullVersion) -> str:
        raise NotImplementedError('subclass must implement')


class DebianCatalog(Catalog):

    # List of official supported architectures obtained from
    # https://www.debian.org/ports/#portlist-released.
    _architectures = {
        'amd64', 'armel', 'armhf', 'i386', 'ia64', 'kfreebsd-amd64',
        'kfreebsd-i386', 'mips', 'mipsel', 'powerpc', 'ppc64el', 's390',
        's390x', 'sparc'
    }

    def __init__(self, old_path: PathExt, new_path: PathExt,
                 subprocess: util.RunCommand,
                 gmtime: _ClockFn) -> None:
        super(DebianCatalog, self).__init__(old_path, new_path, subprocess)
        self._gmtime = gmtime

        # Scan the existing directory hierarchy to find the latest
        # version of all of the packages. We need to know this in order
        # to determine the Epoch and revision number for any new
        # packages we're going to build.
        self._existing = collections.defaultdict(FullVersion)  # type: Dict[str, FullVersion]
        if old_path:
            for root, dirs, files in util.walk(old_path):
                for file in files:
                    parts = file.name.split('_')
                    if len(parts) == 3 and parts[2] == 'all.deb':
                        name = parts[0]
                        version = FullVersion.parse_debian(parts[1])
                        if self._existing[name] < version:
                            self._existing[name] = version

    def _get_filename(self, package: TPkg, version: FullVersion) -> str:
        return '%s_%s_all.deb' % (
            package.get_debian_name(), version.get_debian_version())

    @staticmethod
    def _get_control_snippet(package: TPkg, version: FullVersion,
                             installed_size=None) -> str:
        """Returns a string suitable for writing to a .deb control file.

        For the fields refer to the Debian Policy Manual
        https://www.debian.org/doc/debian-policy/ch-controlfields.html
        """
        snippet = (
            'Package: %(debian_name)s\n'
            'Version: %(version)s\n'
            'Architecture: all\n'
            'Maintainer: %(maintainer)s\n'
            'Description: %(name)s for %(arch)s\n'
            'Homepage: %(homepage)s\n' % {
                'arch': package.get_arch(),
                'homepage': package.get_homepage(),
                'maintainer': package.get_maintainer(),
                'name': package.get_name(),
                'debian_name': package.get_debian_name(),
                'version': version.get_debian_version(),
            })

        # Optional, estimate in kB of disk space needed to install the package
        if installed_size is not None:
            snippet += 'Installed-Size: %d\n' % math.ceil(installed_size / 1024)

        lib_depends = package.get_lib_depends()
        if lib_depends:
            snippet += 'Depends: %s\n' % ', '.join(sorted(
                dep.get_debian_name() for dep in lib_depends))
        return snippet

    def finish(self, private_key: str) -> None:
        # Create package index.
        def write_entry(f, packageTPkg, version: FullVersion):
            f.write(self._get_control_snippet(package, version))
            filename = self._get_filename(package, version)
            path = self._new_path / filename
            f.write(
                'Filename: %s\n'
                'Size: %u\n'
                'SHA256: %s\n' % (
                    filename,
                    path.stat().st_size,
                    util.sha256(path).hexdigest(),
                ))

            f.write('\n')
        index = self._new_path / 'Packages'
        with index.open('wt') as f, lzma.open(index + '.xz', 'wt') as f_xz:
            for package, version in self._packages:
                write_entry(f, package, version)
                write_entry(f_xz, package, version)

        # Link the index into the per-architecture directory.
        (subprocess, gmtime) = self._subprocess, self._gmtime
        for arch in self._architectures:
            index_arch = (
                self._new_path).pathjoin(
                'dists/cloudabi/cloudabi/binary-%s/Packages' % arch)
            util.make_parent_dir(index_arch)
            index.link(index_arch)
            (index + '.xz').link(index_arch + '.xz')
        checksum = util.sha256(index).hexdigest()
        checksum_xz = util.sha256(index + '.xz').hexdigest()
        size = index.stat().st_size
        size_xz = (index + '.xz').stat().st_size
        index.unlink()
        (index + '.xz').unlink()

        # Create the InRelease file.
        with (
            self._new_path / 'dists/cloudabi/InRelease').open('w'
        ) as f, subprocess.Popen([
            'gpg', '--local-user', private_key, '--armor',
            '--sign', '--clearsign', '--digest-algo', 'SHA256',
        ], stdin=PIPE, stdout=f) as proc:
            def append(text: str):
                proc.stdin.write(bytes(text, encoding='ASCII'))
            append(
                'Suite: cloudabi\n'
                'Components: cloudabi\n'
                'Architectures: %s\n'
                'Date: %s\n'
                'SHA256:\n' % (
                ' '.join(sorted(self._architectures)),
                strftime("%a, %d %b %Y %H:%M:%S UTC", gmtime())))
            for arch in sorted(self._architectures):
                append(' %s %d cloudabi/binary-%s/Packages\n' %
                       (checksum, size, arch))
                append(' %s %d cloudabi/binary-%s/Packages.xz\n' %
                       (checksum_xz, size_xz, arch))

    def lookup_latest_version(self, package):
        return self._existing[package.get_debian_name()]

    def package(self, package: TPkg, version: FullVersion) -> PathExt:
        subprocess = self._subprocess
        package.build()
        package.initialize_buildroot({'libarchive', 'llvm'})
        log.info('PKG %s', self._get_filename(package, version))

        platform = self._old_path.platform()
        rootdir = platform(config.DIR_BUILDROOT)
        debian_binary = rootdir / 'debian-binary'
        controldir = rootdir / 'control'
        datadir = rootdir / 'data'

        # Create 'debian-binary' file.
        with debian_binary.open('w') as f:
            f.write('2.0\n')

        def tar(directory: PathExt):
            self._sanitize_permissions(directory)
            self._run_tar([
                '-cJf', str(directory + '.tar.xz'),
                '-C', str(directory),
                '.',
            ])

        # Create 'data.tar.xz' tarball that contains the files that need
        # to be installed by the package.
        prefix = platform(config.USR) / package.get_arch()
        util.make_dir(datadir)
        package.extract(datadir / prefix.relative_to(prefix.root), prefix)
        tar(datadir)

        # Create 'control.tar.xz' tarball that contains the control files.
        util.make_dir(controldir)
        datadir_files = sorted(util.walk_files(datadir))
        datadir_size = sum(fpath.stat().st_size for fpath in datadir_files)
        with (controldir / 'control').open('w') as f:
            f.write(self._get_control_snippet(package, version, datadir_size))
        with (controldir / 'md5sums').open('w') as f:
            f.writelines('%s  %s\n' % (util.md5(fpath).hexdigest(),
                                       fpath.relative_to(datadir))
                         for fpath in datadir_files)
        tar(controldir)

        path = rootdir / 'output.txz'
        subprocess.check_call([str(arg) for arg in [
            rootdir / 'bin/llvm-ar', 'rc', path,
            debian_binary, controldir + '.tar.xz', datadir + '.tar.xz',
        ]])
        return path


class FreeBSDCatalog(Catalog):

    def __init__(self, old_path: PathExt, new_path: PathExt,
                 subprocess: util.RunCommand) -> None:
        super(FreeBSDCatalog, self).__init__(old_path, new_path, subprocess)

        # Scan the existing directory hierarchy to find the latest
        # version of all of the packages. We need to know this in order
        # to determine the Epoch and revision number for any new
        # packages we're going to build.
        self._existing = collections.defaultdict(FullVersion)  # type: Dict[str, FullVersion]
        if old_path:
            for root, dirs, files in util.walk(old_path):
                for file in files:
                    parts = file.name.rsplit('-', 1)
                    if len(parts) == 2 and parts[1].endswith('.txz'):
                        name = parts[0]
                        version = FullVersion.parse_freebsd(parts[1][:-4])
                        if self._existing[name] < version:
                            self._existing[name] = version

    def _get_filename(self, package: TPkg, version: FullVersion) -> str:
        return '%s-%s.txz' % (package.get_freebsd_name(),
                              version.get_freebsd_version())

    def finish(self, private_key: str):
        subprocess = self._subprocess
        subprocess.check_call([
            'pkg', 'repo', str(self._new_path), private_key,
        ])
        # TODO(ed): Copy in some of the old files to keep clients happy.

    def lookup_latest_version(self, package: TPkg) -> FullVersion:
        return self._existing[package.get_freebsd_name()]

    def package(self, package: TPkg, version: FullVersion) -> PathExt:
        package.build()
        package.initialize_buildroot({'libarchive'})
        log.info('PKG %s', self._get_filename(package, version))

        # The package needs to be installed in /usr/local/<arch> on the
        # FreeBSD system.
        platform = self._old_path.platform()
        installdir = platform(config.DIR_BUILDROOT) / 'install'
        arch = package.get_arch()
        prefix = platform(config.USR_LOCAL) / arch
        package.extract(installdir, prefix)
        files = sorted(util.walk_files(installdir))

        # Create the compact manifest.
        base_manifest = (
            '{"name":"%(freebsd_name)s",'
            '"origin":"devel/%(freebsd_name)s",'
            '"version":"%(version)s",'
            '"comment":"%(name)s for %(arch)s",'
            '"maintainer":"%(maintainer)s",'
            '"www":"%(homepage)s",'
            '"abi":"*",'
            '"arch":"*",'
            '"prefix":"/usr/local",'
            '"flatsize":%(flatsize)d,'
            '"desc":"%(name)s for %(arch)s"' % {
                'arch': arch,
                'flatsize': sum(path.lstat().st_size for path in files),
                'freebsd_name': package.get_freebsd_name(),
                'homepage': package.get_homepage(),
                'maintainer': package.get_maintainer(),
                'name': package.get_name(),
                'version': version.get_freebsd_version(),
            })
        deps = package.get_lib_depends()
        if deps:
            base_manifest += ',"deps":{%s}' % ','.join(
                '\"%s\":{"origin":"devel/%s","version":"0"}' % (dep, dep)
                for dep in sorted(pkg.get_freebsd_name() for pkg in deps)
            )
        compact_manifest = platform(config.DIR_BUILDROOT).pathjoin(
                                        '+COMPACT_MANIFEST')
        with compact_manifest.open('w') as f:
            f.write(base_manifest)
            f.write('}')

        # Create the fill manifest.
        if files:
            manifest = platform(config.DIR_BUILDROOT) / '+MANIFEST'
            with manifest.open('w') as f:
                f.write(base_manifest)
                f.write(',"files":{')
                f.write(','.join(
                    '"%s":"1$%s"' % (
                        prefix.pathjoin(path.relative_to(installdir)),
                        util.sha256(path).hexdigest())
                    for path in files))
                f.write('}}')
        else:
            manifest = compact_manifest

        # Create the package.
        output = platform(config.DIR_BUILDROOT) / 'output.tar.xz'
        listing = platform(config.DIR_BUILDROOT) / 'listing'
        with listing.open('w') as f:
            # Leading files in tarball.
            f.write('#mtree\n')
            f.write(
                '+COMPACT_MANIFEST type=file mode=0644 uname=root gname=wheel time=0 contents=%s\n' %
                compact_manifest)
            f.write(
                '+MANIFEST type=file mode=0644 uname=root gname=wheel time=0 contents=%s\n' %
                manifest)
            for path in files:
                fullpath = prefix.pathjoin(path.relative_to(installdir))
                if path.is_symlink():
                    # Symbolic links.
                    f.write(
                        '%s type=link mode=0777 uname=root gname=wheel time=0 link=%s\n' %
                        (fullpath, path.readlink()))
                else:
                    # Regular files.
                    f.write(
                        '%s type=file mode=0%o uname=root gname=wheel time=0 contents=%s\n' %
                        (fullpath, self._get_suggested_mode(path), path))
        self._run_tar(['-cJf', str(output), '-C', str(installdir), '@' + str(listing)])
        return output


class HomebrewCatalog(Catalog):

    _OSX_VERSIONS = {'el_capitan', 'mavericks', 'yosemite'}

    def __init__(self, old_path: PathExt, new_path: PathExt, url: str,
                 subprocess: util.RunCommand) -> None:
        super(HomebrewCatalog, self).__init__(old_path, new_path, subprocess)
        self._url = url

        # Scan the existing directory hierarchy to find the latest
        # version of all of the packages. We need to know this in order
        # to determine the Epoch and revision number for any new
        # packages we're going to build.
        self._existing = collections.defaultdict(FullVersion)  # type: Dict[str, FullVersion]
        if old_path:
            for root, dirs, files in util.walk(old_path):
                for file in files:
                    parts = file.name.split('|', 1)
                    if len(parts) == 2:
                        name = parts[0]
                        version = FullVersion.parse_homebrew(parts[1])
                        if self._existing[name] < version:
                            self._existing[name] = version

    def _get_filename(self, package: TPkg, version: FullVersion) -> str:
        return '%s|%s' % (package.get_homebrew_name(),
                          version.get_homebrew_version())

    @staticmethod
    def _get_classname(name):
        return ''.join(part.capitalize() for part in re.split('[-_]', name))

    def insert(self, package, version, source):
        super(HomebrewCatalog, self).insert(package, version, source)

        # Create symbolic to the tarball for every supported version of
        # Mac OS X.
        filename = self._get_filename(package, version)
        linksdir = self._new_path / 'links'
        util.make_dir(linksdir)
        for osx_version in self._OSX_VERSIONS:
            link = linksdir.pathjoin(
                '%s-%s.%s.bottle.tar.gz' % (
                    package.get_homebrew_name(), version.get_homebrew_version(),
                    osx_version))
            util.remove(link)
            link.symlink_to('..' / filename)

        # Create a formula.
        formulaedir = self._new_path / 'formulae'
        util.make_dir(formulaedir)
        with (formulaedir.pathjoin(
                               package.get_homebrew_name() + '.rb')).open('w') as f:
            # Header.
            f.write("""class %(homebrew_class)s < Formula
  desc "%(name)s for %(arch)s"
  homepage "%(homepage)s"
  url "http://this.package.cannot.be.built.from.source/"
  version "%(version)s"
  revision %(revision)d
""" % {
                'arch': package.get_arch(),
                'homebrew_class': self._get_classname(package.get_homebrew_name()),
                'homepage': package.get_homepage(),
                'name': package.get_name(),
                'revision': version.get_revision(),
                'url': self._url,
                'version': version.get_version(),
            })

            # Dependencies.
            for dep in sorted(pkg.get_homebrew_name()
                              for pkg in package.get_lib_depends()):
                f.write('  depends_on "%s"\n' % dep)

            # Bottles: links to binary packages.
            f.write("""
  bottle do
    root_url "%(url)slinks"
""" % {
                'url': self._url,
            })
            sha256 = util.sha256(source).hexdigest()
            for osx_version in sorted(self._OSX_VERSIONS):
                f.write('    sha256 "%s" => :%s\n' % (sha256, osx_version))
            f.write('  end\nend\n')

    def lookup_latest_version(self, package):
        return self._existing[package.get_homebrew_name()]

    def package(self, package, version):
        package.build()
        package.initialize_buildroot({'libarchive'})
        log.info('PKG %s', self._get_filename(package, version))

        # The package needs to be installed in /usr/local/share/<arch>
        # on the Mac OS X system. In the tarball, pathnames need to be
        # prefixed with <name>/<version>.
        installdir = config.DIR_BUILDROOT / 'install'
        extractdir = installdir.pathjoin(package.get_homebrew_name(),
                                  version.get_homebrew_version())
        util.make_dir(extractdir)
        package.extract(extractdir.pathjoin('share', package.get_arch()),
                        '/usr/local/share'.pathjoin(package.get_arch()))

        # Add a placeholder install receipt file. Homebrew depends on it
        # being present with at least these fields.
        with (extractdir / 'INSTALL_RECEIPT.json').open('w') as f:
            f.write('{"used_options":[],"unused_options":[]}\n')

        # Archive the results.
        self._sanitize_permissions(installdir, directory_mode=0o755)
        output = config.DIR_BUILDROOT / 'output.tar.gz'
        self._run_tar([
            '--options', 'gzip:!timestamp', '-czf', output, '-C', installdir,
            package.get_homebrew_name(),
        ])
        return output


class NetBSDCatalog(Catalog):

    def _get_filename(self, package: TPkg, version: FullVersion) -> str:
        return '%s-%s.tgz' % (package.get_netbsd_name(),
                              version.get_netbsd_version())

    def lookup_latest_version(self, package):
        # TODO(ed): Implement repository scanning.
        return FullVersion()

    def package(self, package, version):
        package.build()
        package.initialize_buildroot({'libarchive'})
        log.info('PKG %s', self._get_filename(package, version))

        # The package needs to be installed in /usr/pkg/<arch> on the
        # NetBSD system.
        installdir = os.path.join(config.DIR_BUILDROOT, 'install')
        arch = package.get_arch()
        prefix = os.path.join('/usr/pkg', arch)
        package.extract(installdir, prefix)
        files = sorted(util.walk_files(installdir))

        # Package contents list.
        util.make_dir(installdir)
        with installdir.pathjoin('+CONTENTS').open('w') as f:
            f.write(
                '@cwd /usr/pkg/%s\n'
                '@name %s-%s\n' % (
                    arch, package.get_netbsd_name(),
                    version.get_netbsd_version()))
            for dep in sorted(pkg.get_netbsd_name()
                              for pkg in package.get_lib_depends()):
                f.write('@pkgdep %s-[0-9]*\n' % dep)
            for path in files:
                f.write(path.relative_to(installdir) + '\n')

        # Package description.
        with installdir.pathjoin('+COMMENT').open('w') as f:
            f.write('%s for %s\n' % (package.get_name(), package.get_arch()))
        with installdir.pathjoin('+DESC').open('w') as f:
            f.write(
                '%(name)s for %(arch)s\n'
                '\n'
                'Homepage:\n'
                '%(homepage)s\n' % {
                    'arch': package.get_arch(),
                    'name': package.get_name(),
                    'homepage': package.get_homepage(),
                }
            )

        # Build information file.
        # TODO(ed): We MUST specify a machine architecture and operating
        # system, meaning that these packages are currently only
        # installable on NetBSD/x86-64. Figure out a way we can create
        # packages that are installable on any system that uses pkgsrc.
        with installdir.pathjoin('+BUILD_INFO').open('w') as f:
            f.write(
                'MACHINE_ARCH=x86_64\n'
                'PKGTOOLS_VERSION=00000000\n'
                'OPSYS=NetBSD\n'
                'OS_VERSION=\n'
            )

        self._sanitize_permissions(installdir)
        output = config.DIR_BUILDROOT / 'output.tar.xz'
        listing = config.DIR_BUILDROOT / 'listing'
        with listing.open('w') as f:
            f.write('+CONTENTS\n+COMMENT\n+DESC\n+BUILD_INFO\n')
            for path in files:
                f.write(path.relative_to(installdir) + '\n')
        self._run_tar(['-cJf', output, '-C', installdir, '-T', listing])
        return output


class OpenBSDCatalog(Catalog):

    def _get_filename(self, package: TPkg, version: FullVersion) -> str:
        return '%s-%s.tgz' % (package.get_openbsd_name(),
                              version.get_openbsd_version())

    def lookup_latest_version(self, package):
        # TODO(ed): Implement repository scanning.
        return FullVersion()

    def package(self, package, version):
        package.build()
        package.initialize_buildroot({'libarchive'})
        log.info('PKG %s', self._get_filename(package, version))

        # The package needs to be installed in /usr/local/<arch> on the
        # OpenBSD system.
        installdir = config.DIR_BUILDROOT / 'install'
        arch = package.get_arch()
        prefix = '/usr/local' / arch
        package.extract(installdir, prefix)
        files = sorted(util.walk_files(installdir))

        # Package contents list.
        contents = config.DIR_BUILDROOT / 'contents'
        with contents.open('w') as f:
            f.write(
                '@name %s-%s\n'
                '@cwd %s\n' % (
                    package.get_openbsd_name(), version.get_openbsd_version(),
                    prefix))
            # TODO(ed): Encode dependencies.
            written_dirs = set()
            for path in files:
                # Write entry for parent directories.
                relpath = path.relative_to(installdir)
                fullpath = ''
                for component in relpath.parent.split('/'):
                    fullpath += component + '/'
                    if fullpath not in written_dirs:
                        f.write(fullpath + '\n')
                        written_dirs.add(fullpath)

                if path.is_symlink():
                    # Write entry for symbolic link.
                    f.write(
                        '%s\n'
                        '@symlink %s\n' % (relpath, path.readlink()))
                else:
                    # Write entry for regular file.
                    f.write(
                        '%s\n'
                        '@sha %s\n'
                        '@size %d\n' % (
                            relpath,
                            str(base64.b64encode(
                                util.sha256(path).digest()), encoding='ASCII'),
                            path.lstat().st_size))

        # Package description.
        desc = config.DIR_BUILDROOT / 'desc'
        with desc.open('w') as f:
            f.write(
                '%(name)s for %(arch)s\n'
                '\n'
                'Maintainer: %(maintainer)s\n'
                '\n'
                'WWW:\n'
                '%(homepage)s\n' % {
                    'arch': package.get_arch(),
                    'name': package.get_name(),
                    'maintainer': package.get_maintainer(),
                    'homepage': package.get_homepage(),
                }
            )

        output = config.DIR_BUILDROOT / 'output.tar.gz'
        listing = config.DIR_BUILDROOT / 'listing'
        with listing.open('w') as f:
            # Leading files in tarball.
            f.write('#mtree\n')
            f.write(
                '+CONTENTS type=file mode=0666 uname=root gname=wheel time=0 contents=%s\n' %
                contents)
            f.write(
                '+DESC type=file mode=0666 uname=root gname=wheel time=0 contents=%s\n' %
                desc)
            for path in files:
                relpath = path.relative_to(installdir)
                if path.is_symlink():
                    # Symbolic links need to use 0o555 on OpenBSD.
                    f.write(
                        '%s type=link mode=0555 uname=root gname=wheel time=0 link=%s\n' %
                        (relpath, path.readlink()))
                else:
                    # Regular files.
                    f.write(
                        '%s type=file mode=0%o uname=root gname=wheel time=0 contents=%s\n' %
                        (relpath, self._get_suggested_mode(path), path))
        self._run_tar([
            '--options', 'gzip:!timestamp', '-czf', output, '-C', installdir,
            '@' + listing,
        ])
        return output


class ArchLinuxCatalog(Catalog):

    def __init__(self, old_path: PathExt, new_path: PathExt,
                 subprocess: util.RunCommand,
                 chdir: Callable[[PathExt], None]) -> None:
        super(ArchLinuxCatalog, self).__init__(old_path, new_path, subprocess)
        self._chdir = chdir

        self._existing = collections.defaultdict(FullVersion)  # type: Dict[str, FullVersion]
        if old_path:
            for root, dirs, files in util.walk(old_path):
                for file in files:
                    parts = file.name.rsplit('-', 3)
                    if len(parts) == 4 and parts[3] == 'any.pkg.tar.xz':
                        name = parts[0]
                        version = FullVersion.parse_archlinux(parts[1] + '-' + parts[2])
                        if self._existing[name] < version:
                            self._existing[name] = version

    def _get_filename(self, package: TPkg, version: FullVersion) -> str:
        return '%s-%s-any.pkg.tar.xz' % (package.get_archlinux_name(),
                                         version.get_archlinux_version())

    @staticmethod
    def _get_suggested_mode(path):
        return Catalog._get_suggested_mode(path) | 0o200

    def lookup_latest_version(self, package):
        return self._existing[package.get_archlinux_name()]

    def package(self, package, version):
        package.build()
        package.initialize_buildroot({'libarchive'})
        log.info('PKG %s', self._get_filename(package, version))

        installdir = config.DIR_BUILDROOT / 'install'
        arch = package.get_arch()
        prefix = '/usr' / arch
        package.extract(installdir / prefix[1:], prefix)
        files = sorted(util.walk_files(installdir))

        util.make_dir(installdir)
        pkginfo = installdir / '.PKGINFO'
        with pkginfo.open('w') as f:
            f.write(
                'pkgname = %(archlinux_name)s\n'
                'pkgdesc = %(name)s for %(arch)s\n'
                'pkgver = %(version)s\n'
                'size = %(flatsize)s\n'
                'arch = any\n' % {
                    'arch': package.get_arch(),
                    'archlinux_name': package.get_archlinux_name(),
                    'flatsize': sum(path.lstat().st_size for path in files),
                    'name': package.get_name(),
                    'version': version.get_archlinux_version(),
                }
            )
            for dep in sorted(pkg.get_archlinux_name() for pkg in package.get_lib_depends()):
                f.write('depend = %s\n' % dep)

        output = config.DIR_BUILDROOT / 'output.tar.xz'
        listing = config.DIR_BUILDROOT / 'listing'
        with listing.open('w') as f:
            f.write('.PKGINFO\n')
            for path in files:
                f.write(path.relative_to(installdir) + '\n')

        mtree = installdir / '.MTREE'

        with listing.open('w') as f:
            f.write('#mtree\n')
            f.write(
                '.PKGINFO type=file mode=0644 uname=root gname=root time=0 contents=%s\n' % pkginfo)
            f.write(
                '.MTREE type=file mode=0644 uname=root gname=root time=0 contents=%s\n' % mtree)
            for path in files:
                relpath = path.relative_to(installdir)
                if path.is_symlink():
                    f.write(
                        '%s type=link mode=0777 uname=root gname=root time=0 link=%s\n' %
                        (relpath, path.readlink()))
                else:
                    f.write(
                        '%s type=file mode=0%o uname=root gname=root time=0 contents=%s\n' %
                        (relpath, self._get_suggested_mode(path), path))

        self._run_tar([
            '-czf', mtree,
            '-C', installdir,
            '--format=mtree',
            '--options=gzip:!timestamp,!all,use-set,type,uid,gid,mode,time,size,md5,sha256,link',
            '--exclude=.MTREE',
            '@' + listing])

        self._run_tar(['-cJf', output, '-C', installdir, '@' + listing])

        return output

    def finish(self, private_key):
        subprocess = self._subprocess
        for package, version in self._packages:
            package_file = self._get_filename(package, version)
            subprocess.check_call([
                'gpg', '--detach-sign', '--local-user', private_key,
                '--no-armor', '--digest-algo', 'SHA256',
                self._new_path / package_file])
        db_file = self._new_path / 'cloudabi-ports.db.tar.xz'
        packages = [self._new_path.pathjoin(self._get_filename(*p)) for p in self._packages]
        # Ensure that repo-add as a valid working directory.
        self._chdir('/')
        subprocess.check_call(['repo-add', '-s', '-k', private_key, db_file] + packages)


class CygwinCatalog(Catalog):

    def __init__(self, old_path: PathExt, new_path: PathExt,
                 subprocess: util.RunCommand) -> None:
        super(CygwinCatalog, self).__init__(old_path, new_path, subprocess)

        self._existing = collections.defaultdict(FullVersion)  # type: Dict[str, FullVersion]
        if old_path:
            for root, dirs, files in util.walk(old_path):
                for file in files:
                    if file.name.endswith('.tar.xz'):
                        parts = file.name[:-7].rsplit('-', 2)
                        if len(parts) == 3:
                            name = parts[0]
                            version = FullVersion.parse_cygwin(parts[1] + '-' + parts[2])
                            if self._existing[name] < version:
                                self._existing[name] = version

    def _get_filename(self, package: TPkg, version: FullVersion) -> str:
        return '%s-%s.tar.xz' % (package.get_cygwin_name(),
                                 version.get_cygwin_version())

    def lookup_latest_version(self, package):
        return self._existing[package.get_cygwin_name()]

    def package(self, package: TPkg, version: FullVersion):
        package.build()
        package.initialize_buildroot({'libarchive'})
        log.info('PKG %s', self._get_filename(package, version))

        platform = self._old_path.platform()
        installdir = platform(config.DIR_BUILDROOT) / 'install'
        arch = package.get_arch()
        prefix = platform(config.USR) / arch
        package.extract(installdir / prefix.relative_to(prefix.root), prefix)

        util.make_dir(installdir)

        output = platform(config.DIR_BUILDROOT) / 'output.tar.xz'

        self._run_tar(['-cJf', str(output), '-C', str(installdir), '.'])

        return output

    def finish(self, private_key: str):
        for cygwin_arch in ('x86', 'x86_64'):
            cygwin_arch_dir = self._new_path / cygwin_arch
            util.make_dir(cygwin_arch_dir)
            setup_file = cygwin_arch_dir / 'setup.ini'
            with setup_file.open('w') as f:
                f.write('release: cygwin\n')
                f.write('arch: %s\n' % cygwin_arch)
                f.write('setup-timestamp: %d\n' % int(time.time()))
                for package, version in sorted(self._packages,
                    key=lambda p: p[0].get_cygwin_name()):
                    package_file_name = self._get_filename(package, version)
                    package_file = self._new_path / package_file_name
                    f.write(
                        '\n'
                        '@ %(cygwinname)s\n'
                        'sdesc: "%(name)s for %(arch)s"\n'
                        'version: %(version)s\n'
                        'category: CloudABI\n' % {
                            'cygwinname': package.get_cygwin_name(),
                            'arch': package.get_arch(),
                            'name': package.get_name(),
                            'version': version.get_cygwin_version(),
                        }
                    )
                    if len(package.get_lib_depends()) > 0:
                        f.write('requires: %(deps)s\n' % {
                            'deps': ' '.join(sorted(pkg.get_cygwin_name() for pkg in
                                package.get_lib_depends()))
                        })
                    f.write(
                        'install: %(filename)s %(size)s %(sha512)s\n' % {
                            'size': package_file.lstat().st_size,
                            'filename': package_file_name,
                            'sha512': util.sha512(package_file).hexdigest(),
                        }
                    )
            subprocess.check_call([
                'gpg', '--sign', '--detach-sign', '--local-user', private_key,
                '--batch', '--yes', setup_file,
            ])


class RedHatCatalog(Catalog):

    def _get_filename(self, package: TPkg, version: FullVersion) -> str:
        return '%s-%s.noarch.rpm' % (package.get_redhat_name(),
                                     version.get_redhat_version())

    @staticmethod
    def _file_linkto(filename):
        try:
            return filename.readlink()
        except OSError:
            return ''

    @staticmethod
    def _file_md5(filename):
        if filename.is_symlink():
            return ''
        else:
            return util.md5(filename).hexdigest()

    @staticmethod
    def _file_mode(filename):
        mode = filename.lstat().st_mode
        if stat.S_ISLNK(mode):
            # Symbolic links.
            return 0o120777 - 65536
        elif stat.S_ISDIR(mode) or (mode & 0o111) != 0:
            # Directories and executable files.
            return 0o100555 - 65536
        else:
            # Non-executable files.
            return 0o100444 - 65536

    @staticmethod
    def _file_size(filename):
        sb = filename.lstat()
        if stat.S_ISREG(sb.st_mode):
            return sb.st_size
        return 0

    def lookup_latest_version(self, package):
        # TODO(ed): Implement repository scanning.
        return FullVersion()

    def package(self, package, version):
        package.build()
        package.initialize_buildroot({'libarchive'})
        log.info('PKG %s', self._get_filename(package, version))

        # The package needs to be installed in /usr/arch> on the Red Hat
        # system.
        installdir = config.DIR_BUILDROOT / 'install'
        arch = package.get_arch()
        prefix = '/usr' / arch
        package.extract(installdir, prefix)
        files = sorted(util.walk_files(installdir))

        # Create an xz compressed cpio payload containing all files.
        listing = config.DIR_BUILDROOT / 'listing'
        with listing.open('w') as f:
            f.write('#mtree\n')
            for path in files:
                relpath = prefix.pathjoin(path.relative_to(installdir))
                if path.is_symlink():
                    f.write(
                        '%s type=link mode=0777 uname=root gname=root time=0 link=%s\n' %
                        (relpath, path.readlink()))
                else:
                    f.write(
                        '%s type=file mode=0%o uname=root gname=root time=0 contents=%s\n' %
                        (relpath, self._get_suggested_mode(path), path))
        data = config.DIR_BUILDROOT / 'data.cpio.xz'
        self._run_tar([
            '-cJf', data, '--format=newc', '-C', installdir, '@' + listing,
        ])

        # The header, based on the following documentation:
        # http://www.rpm.org/max-rpm/s1-rpm-file-format-rpm-file-format.html
        # http://refspecs.linux-foundation.org/LSB_5.0.0/LSB-Core-generic/LSB-Core-generic/pkgformat.html
        name = package.get_redhat_name()
        lib_depends = sorted(dep.get_redhat_name()
                             for dep in package.get_lib_depends())
        dirs = sorted({f.parent for f in files})
        header = bytes(rpm.Header({
            100: rpm.StringArray(['C']),
            1000: rpm.String(name),
            1001: rpm.String(str(version.get_version())),
            1002: rpm.String(str(version.get_revision())),
            1003: rpm.Int32([version.get_epoch()]),
            1004: rpm.I18NString('%s for %s' % (name, arch)),
            1005: rpm.I18NString('%s for %s' % (name, arch)),
            1009: rpm.Int32([sum(self._file_size(f) for f in files)]),
            1014: rpm.String('Unknown'),
            1016: rpm.I18NString('Development/Libraries'),
            1020: rpm.String(package.get_homepage()),
            1021: rpm.String('linux'),
            1022: rpm.String('noarch'),
            1028: rpm.Int32(f.lstat().st_size for f in files),
            1030: rpm.Int16(self._file_mode(f) for f in files),
            1033: rpm.Int16(0 for f in files),
            1034: rpm.Int32(0 for f in files),
            1035: rpm.StringArray(self._file_md5(f) for f in files),
            1036: rpm.StringArray(self._file_linkto(f) for f in files),
            1037: rpm.Int32(0 for f in files),
            1039: rpm.StringArray('root' for f in files),
            1040: rpm.StringArray('root' for f in files),
            1047: rpm.StringArray([name]),
            1048: rpm.Int32(0 for dep in lib_depends),
            1049: rpm.StringArray(lib_depends),
            1050: rpm.StringArray('' for dep in lib_depends),
            1095: rpm.Int32(1 for f in files),
            1096: rpm.Int32(range(1, len(files) + 1)),
            1097: rpm.StringArray('' for f in files),
            1112: rpm.Int32(8 for dep in lib_depends),
            1113: rpm.StringArray([version.get_redhat_version()]),
            1116: rpm.Int32(dirs.index(f.parent) for f in files),
            1117: rpm.StringArray(f.name for f in files),
            1118: rpm.StringArray(prefix.pathjoin(
                                               d.relative_to(installdir)) +
                                  '/'
                                  for d in dirs),
            1124: rpm.String('cpio'),
            1125: rpm.String('xz'),
            1126: rpm.String('9'),
        }))

        # The signature.
        checksum = hashlib.md5()
        checksum.update(header)
        util.hash_file(data, checksum)
        signature = bytes(rpm.Header({
            1000: rpm.Int32([len(header) + data.stat().st_size]),
            1004: rpm.Bin(checksum.digest()),
        }))

        # Create the RPM file.
        output = config.DIR_BUILDROOT / 'output.rpm'
        with output.open('wb') as f:
            # The lead.
            f.write(b'\xed\xab\xee\xdb\x03\x00\x00\x00\x00\x00')
            fullname = '%s-%s' % (name, version.get_redhat_version())
            f.write(bytes(fullname, encoding='ASCII')[:66].ljust(66, b'\0'))
            f.write(b'\x00\x01\x00\x05')
            f.write(b'\x00' * 16)

            # The signature, aligned up to eight bytes in size.
            f.write(signature)
            f.write(b'\0' * ((8 - len(signature) % 8) % 8))

            # The header.
            f.write(header)

            # The payload.
            with data.open('rb') as fin:
                shutil.copyfileobj(fin, f)
        return output

    def finish(self, private_key):
        subprocess.check_call(['createrepo', self._new_path])
        subprocess.check_call([
            'gpg', '--detach-sign', '--local-user', private_key,
            '--armor', self._new_path / 'repodata/repomd.xml',
        ])

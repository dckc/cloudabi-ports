# Copyright (c) 2015-2017 Nuxi, https://nuxi.nl/
#
# SPDX-License-Identifier: BSD-2-Clause


def build(ctx):
    build = ctx.extract().gnu_configure(['--without-iconv'])
    build.make()
    build.make_install().install()


host_package(
    name='libarchive',
    version='3.3.2',
    homepage='http://www.libarchive.org/',
    lib_depends={'xz'},
    build_cmd=build)

distfile(
    name='libarchive-3.3.2.tar.gz',
    checksum='ed2dbd6954792b2c054ccf8ec4b330a54b85904a80cef477a1c74643ddafa0ce',
    master_sites={'http://www.libarchive.org/downloads/'})

# Copyright (c) 2017 Nuxi, https://nuxi.nl/
#
# SPDX-License-Identifier: BSD-2-Clause


def build(ctx):
    build = ctx.extract('cares-1_13_0').cmake(['-DCARES_BUILD_TOOLS=OFF'])
    build.ninja()
    build.ninja_install().install()


package(
    name='c-ares',
    version='1.13.0',
    homepage='https://c-ares.haxx.se/',
    lib_depends={'c-runtime'},
    build_cmd=build)

host_package(
    name='c-ares',
    version='1.13.0',
    homepage='https://c-ares.haxx.se/',
    build_depends={'cmake', 'ninja'},
    build_cmd=build)

distfile(
    name='cares-1_13_0.tar.gz',
    checksum='7c48c57706a38691041920e705d2a04426ad9c68d40edd600685323f214b2d57',
    master_sites={'https://github.com/c-ares/c-ares/archive/'},
    unsafe_string_sources={
        'ares_gethostbyaddr.c', 'ares_getnameinfo.c', 'ares_options.c',
        'ares_parse_ns_reply.c'
    })

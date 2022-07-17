#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages


with open('README.md') as f:
    readme = f.read()

PACKAGE_NAME = 'bdnex'

INSTALL_REQUIRES = [
    'jsonmerge',
    'numpy',
]

PACKAGE_DATA = {
    'bdnex.conf': ['*.json'],
    'bdnex.conf': ['*.xsd'],
}

PACKAGE_EXCLUDES = ['tests*']

TESTS_REQUIRE = [
    'pytest',
    'ipython',
    'ipdb'
]

EXTRAS_REQUIRE = {
    'testing': TESTS_REQUIRE,
    'interactive': TESTS_REQUIRE
}

setup(
    name=PACKAGE_NAME,
    version='0.1.0',
    description='BD next generation metadata scrapper',
    long_description=readme,
    author='Laurent Besnard',
    author_email='laurent.besnard@utas.edu.au',
    url='https://github.com/lbesnard/bdnex',
    install_requires=INSTALL_REQUIRES,
    packages=find_packages(exclude=PACKAGE_EXCLUDES),
    package_data=PACKAGE_DATA,
    test_suite='tests_bdnex',
    tests_require=TESTS_REQUIRE,
    extras_require=EXTRAS_REQUIRE,
    zip_safe=False,
    scripts=['bdnex/bdnex.py'],
    python_requires='>3.8',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: Implementation :: CPython',
    ]
)

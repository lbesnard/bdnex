#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from setuptools import setup


def _read(fn):
    path = os.path.join(os.path.dirname(__file__), fn)
    return open(path).read()


setup(
    name='bdnex',
    version='0.1',
    description='BD (french comic) tagger and library organizer',
    author='Laurent Besnard',
    author_email='besnard.laurent@gmail.com',
    url='https://bdnex.io/',
    license='MIT',
    platforms='ALL',
    long_description=_read('README.md'),
    test_suite='test',
    zip_safe=False,
    include_package_data=True,  # Install plugin resources.

    packages=[
        'bdnex',
        'bdnex.conf',
        'bdnex.lib',
        'bdnex.ui'
    ],
    package_data={  # Optional
        "bdnex.conf": ["bdgest_mapping.conf",
                       "ComicInfo.xsd"],
    },
    entry_points={
        'console_scripts': [
            'bdnex = bdnex.ui:main',
        ],
    },

    install_requires=[
        'rarfile',
        'beautifulsoup4',
        'numpy',
        'thefuzz',
        'rapidfuzz',
        'duckduckgo-search',
        'argparse',
        'termcolor',
        #'opencv-contrib-python',
        'imutils',
        'tenacity',
        'opencv-contrib-python-headless',
        'pandas',
    ],

    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Environment :: Console',
        'Environment :: Web Environment',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: Implementation :: CPython',
    ],
)
===============================================
Sample data handling (:mod:`peregrine.samples`)
===============================================

.. currentmodule:: peregrine


Samples data files
==================

The :mod:`peregrine.samples` module provides functions for handling sample data
files. Currently only binary sample data stored as an 8-bit signed integer
array is supported.


Saving samples
--------------

Samples can be saved to a file using the :func:`samples.save_samples` function.
Its usage is best illustrated by an example:

.. ipython::

  In [21]: import numpy as np

  In [22]: import peregrine.samples

  In [23]: samples = np.arange(-7, 8)

  In [24]: samples
  Out[24]: array([-7, -6, -5, -4, -3, -2, -1,  0,  1,  2,  3,  4,  5,  6,  7])

  In [26]: len(samples)
  Out[26]: 15

  In [25]: peregrine.samples.save_samples("samples_file", samples)


Loading samples
---------------

Samples can be loaded from a file using the :func:`samples.load_samples`
function.

By default the whole file is read in:

.. ipython::

  In [28]: peregrine.samples.load_samples("samples_file")
  Out[28]: array([-7, -6, -5, -4, -3, -2, -1,  0,  1,  2,  3,  4,  5,  6,  7], dtype=int8)

Or an explicit number of samples can be specified. When an explicit number is
specified, :func:`load_samples` will always return that number of samples or if
that number of samples cannot be read then an exception will be raised:

.. ipython::

  In [29]: peregrine.samples.load_samples("samples_file", 10)
  Out[29]: array([-7, -6, -5, -4, -3, -2, -1,  0,  1,  2], dtype=int8)

  @verbatim
  In [30]: peregrine.samples.load_samples("samples_file", 16)
  ---------------------------------------------------------------------------
  EOFError                                  Traceback (most recent call last)
    ...
  EOFError: Failed to read 16 samples from file 'samples_file'

A number of samples at the beginning of the file can be discarded using the
`num_skip` parameter:

.. ipython::

  In [32]: peregrine.samples.load_samples("samples_file", -1, 5)
  Out[32]: array([-2, -1,  0,  1,  2,  3,  4,  5,  6,  7], dtype=int8)

  In [33]: peregrine.samples.load_samples("samples_file", 5, 5)
  Out[33]: array([-2, -1,  0,  1,  2], dtype=int8)


Sample data analysis
====================

The :mod:`peregrine.analysis.samples` module provides various functions for
analysing and plotting sample data.

.. ipython::

  In [1]: import peregrine.samples

  In [2]: import peregrine.analysis.samples

  In [3]: samps = peregrine.samples.load_samples("../tests/test_samples.dat")

  @savefig samples_analysis_summary.png width=75% align=center
  In [13]: peregrine.analysis.samples.summary(samps, 16.368e6)


Command-line utility
--------------------

The functions in the :mod:`peregrine.analysis.samples` module are also exposed
though the command-line utility ``peregrine-analyze-samples``.

Usage information can be found by running::

  $ peregrine-analyze-samples --help


Reference / API
===============


:mod:`peregrine.samples` Module
-------------------------------

.. automodule:: peregrine.samples

  .. rubric:: Functions

  .. autosummary::
    :toctree: api

    save_samples
    load_samples


:mod:`peregrine.analysis.samples` Module
----------------------------------------

.. automodule:: peregrine.analysis.samples

  .. rubric:: Functions

  .. autosummary::
    :toctree: api

    summary
    hist
    psd


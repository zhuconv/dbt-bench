#!/usr/bin/env python3
"""Compile a real extension through pinned Triton; GPU execution is a separate check."""
import json
import os
import shutil
import subprocess

from triton.runtime.build import compile_module_from_src

SOURCE = r'''
#include <Python.h>
static PyObject *answer(PyObject *self, PyObject *args) { return PyLong_FromLong(37); }
static PyMethodDef methods[] = {{"answer", answer, METH_NOARGS, "compiler probe"}, {NULL, NULL, 0, NULL}};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "dbt_bench_compiler_probe", NULL, -1, methods};
PyMODINIT_FUNC PyInit_dbt_bench_compiler_probe(void) { return PyModule_Create(&module); }
'''

if __name__ == '__main__':
    compiler = os.environ['CC']
    version = subprocess.run([compiler, '--version'], check=True, capture_output=True, text=True)
    ninja = shutil.which('ninja')
    if not ninja:
        raise RuntimeError('ninja is absent from the model server PATH')
    ninja_version = subprocess.run([ninja, '--version'], check=True, capture_output=True, text=True)
    module = compile_module_from_src(SOURCE, 'dbt_bench_compiler_probe')
    assert module.answer() == 37
    print(json.dumps({'passed':True, 'compiler':compiler, 'version':version.stdout.splitlines()[0],
                      'extension_result':module.answer(), 'gpu_test':False,
                      'ninja':ninja, 'ninja_version':ninja_version.stdout.strip(),
                      'scope':'Actual pinned Triton host extension compilation and loading, including its native -Wno-psabi argument'}))

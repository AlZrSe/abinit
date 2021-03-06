# coding: utf-8
"""
"""
from __future__ import print_function, division, unicode_literals, absolute_import

import os
import re
import time
import shutil
import pickle
import difflib

from collections import OrderedDict, defaultdict
from textwrap import TextWrapper
from pprint import pprint, pformat
from .parser import FortranKissParser
from .tools import lazy_property
from .termcolor import cprint


EXTERNAL_MODS = {
    # Intrinsics
    "iso_fortran_env",
    "iso_c_binding",
    "ieee_arithmetic",
    "ieee_exceptions",
    "ieee_features",
    # Modules provided by compilers.
    "f90_unix_proc",
    "f90_unix_dir",
    "ifcore",
    # MPI modules.
    "mpi",
    "mpi_f08",
    # External libraries.
    "openacc",
    "omp_lib",
    "mkl_dfti",
    "netcdf",
    "etsf_io_low_level",
    "etsf_io",
    "plasma",
    "elpa",
    "elpa1",
    "fox_sax",
    "m_libpaw_libxc_funcs",
    "m_psml",
    "m_psml_api",
    # Bigdft modules.
    "yaml_output",
    "bigdft_api",
    "module_base",
    "module_types",
    "module_xc",
    "poisson_solver",
    "dynamic_memory",
    # Abinit-specific modules.
    "m_build_info",
    "m_optim_dumper",
    "libxc_functionals",
}


class FortranFile(object):
    """
    Base class for files containing Fortran source code.
    A FortranFile can have modules, programs, subroutines and functions.

    .. attributes:

        modules:
        programs:
        subroutines
        functions
    """

    @classmethod
    def from_path(cls, path, macros=None, verbose=0):
        if macros == "abinit": macros = AbinitProject.MACROS
        p = FortranKissParser(macros=macros, verbose=verbose).parse_file(path)

        new = cls(path)
        new.all_includes = p.all_includes
        new.programs = p.programs
        new.modules = p.modules
        new.subroutines = p.subroutines
        new.functions = p.functions

        return new

    def __init__(self, path):
        # A file can contain multiples modules but not procedures outside modules
        # A file with program cannot contain other procedures/modules outside program
        # module/program must be followed by end [module|program] to facilitate parsing.
        self.path = os.path.abspath(path)
        # TODO name --> basename and REMOVE self.name
        self.name = os.path.basename(self.path)
        self.basename = self.name
        self.dirname = os.path.dirname(self.path)

        # Save initial stat values, used to understand if reload is needed.
        self.stat = os.stat(self.path)

        self.programs, self.modules, self.subroutines, self.functions = [], [], [], []

        #self.uses, self.includes = [], []
        self.all_used_mods = []
        self.all_usedby_mods = []
        #self.num_f90lines, self.num_doclines = 0, 0

    #@lazy_property
    #def all_used_mods(self):
    #    all_mods = []
    #    for a in ["programs", "modules", "subroutines", "functions"]:
    #        for p in getattr(self, a):
    #            all_mods.extend(p.local_uses)
    #    return sorted(set(all_mods))

    #@lazy_property
    #def all_usedby_mods(self):
    #    all_mods = []
    #    for a in ["programs", "modules", "subroutines", "functions"]:
    #        for p in getattr(self, a):
    #            all_mods.extend(p.usedby_mods)
    #    return sorted(set(all_mods))

    def __eq__(self, other):
        if other is None: return False
        if not isinstance(other, self.__class__): return False
        return self.path == other.path

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.path)

    def stree(self):
        """
        Return string with textual representation of the tree.
        """
        lines = [repr(self)]; app = lines.append
        for a in ["programs", "modules", "subroutines", "functions"]:
            for p in getattr(self, a):
                app(p.stree())

        return "\n".join(lines)

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self.path)

    def __str__(self):
        return self.to_string()

    def to_string(self, verbose=0, with_dataframe=True, width=90):
        """
        String representation with verbosity level `verbose`.
        Text is wrapped at `width` columns.
        """
        w = TextWrapper(initial_indent="\t", subsequent_indent="\t", width=width)
        lines = []; app = lines.append
        app("%s:\n\t%s\n" % (self.__class__.__name__, os.path.relpath(self.path)))
        app("Use modules:\n%s\n" % w.fill(", ".join(mod.name for mod in self.all_used_mods)))
        app("Use dir_levels:\n%s\n" % w.fill(", ".join(map(str, sorted(set(mod.dirlevel for mod in self.all_used_mods))))))
        app("Includes:\n%s\n" % w.fill(", ".join(self.all_includes)))
        app("Used by modules:\n%s\n" % w.fill(", ".join(mod.name for mod in self.all_usedby_mods)))
        app("Used by dir_levels:\n%s\n" % w.fill(", ".join(map(str, sorted(set(mod.dirlevel for mod in self.all_usedby_mods))))))

        if verbose:
            for a in ["programs", "modules", "subroutines", "functions"]:
                plist = getattr(self, a)
                if not plist: continue
                app("Fortran file contains %d %s(s)\n" % (len(plist), a[:-1]))
                for p in plist:
                    app(p.to_string(verbose=verbose, width=width))

        if with_dataframe:
            df = self.get_stats(as_dataframe=True)
            app(df.to_string())

        if verbose > 1:
            app(self.stree())

        return "\n".join(lines)

    @lazy_property
    def all_uses(self):
        """
        List with full list of modules (string) used by the Fortran file
        Includes modules used by the procedures declared in the file.
        """
        all_uses = []
        for a in ["programs", "modules", "subroutines", "functions"]:
            for p in getattr(self, a):
                all_uses.extend(p.uses)
        return sorted(set(all_uses))

    @lazy_property
    def dirlevel(self):
        # 72_response --> 72
        return int(os.path.basename(os.path.dirname(self.path)).split("_")[0])

    @lazy_property
    def min_dirlevel(self):
        return max(mod.dirlevel for mod in self.all_used_mods) if self.all_used_mods else 999

    @lazy_property
    def max_dirlevel(self):
        return min(mod.dirlevel for mod in self.all_usedby_mods) if self.all_usedby_mods else 0

    def iter_procedures(self, visibility="public"):
        for a in ["modules", "programs", "subroutines", "functions"]:
            for container in getattr(self, a):
                for p in container.public_procedures:
                    yield p

    def find_public_entity(self, name, all_names=None):
        """
        Find and return the public procedure or datatype with `name`.
        Return None if not found.
        """
        for a in ["modules", "programs", "subroutines", "functions"]:
            for p in getattr(self, a):
                for e in p.public_procedures:
                    if e.name == name: return e
                    if all_names is not None: all_names.append(e.name)
                if a == "modules":
                    # Try also in datatypes and interfaces.
                    for e in p.types:
                        if e.name == name: return e
                        if all_names is not None: all_names.append(e.name)
                    for e in p.interfaces:
                        if e.name == name: return e
                        if all_names is not None: all_names.append(e.name)

        return None

    def find_datatype(self, name, all_names=None):
        """
        Find and return the public datatype with `name`.
        Return None if not found.
        """
        for p in getattr(self, "modules"):
            for e in p.types:
                if e.name == name: return e
                if all_names is not None: all_names.append(e.name)

        return None

    def get_stats(self, as_dataframe=False):
        """
        Return dictionary with FortFile stats or pandas dataframe if as_dataframe.
        """
        d = OrderedDict([
            ("use", len(self.all_used_mods)),
            ("usedby", len(self.all_usedby_mods)),
            ("min_dirlevel", self.min_dirlevel),
            ("this_dirlevel", self.dirlevel),
            ("max_dirlevel", self.max_dirlevel),
            ("includes", len(self.all_includes)),
            #("subroutines", len(self.subroutines)),
            #("functions", len(self.functions)),
            #("modules", len(self.modules)),
            #("programs", len(self.programs)),
            #("code_lines", self.num_f90lines),
            #("doc_lines", self.num_doclines),
        ])

        if not as_dataframe: return d
        import pandas as pd
        return pd.DataFrame([d], index=[self.basename], columns=d.keys() if d else None)

    def check_abirules(self, verbose=0):
        retcode = 0
        for a in ["modules", "programs", "subroutines", "functions"]:
            for p in getattr(self, a):
                retcode += p.check_abirules(verbose=verbose)

        return retcode

    #def write_notebook(self, nbpath=None):
    #    """
    #    Write a jupyter_ notebook to ``nbpath``. If nbpath is None, a temporay file in the current
    #    working directory is created. Return path to the notebook.
    #    """
    #    nbformat, nbv, nb = self.get_nbformat_nbv_nb(title=None)

    #    nb.cells.extend([
    #        nbv.new_markdown_cell("## %s" % title)),
    #        nbv.new_code_cell("gsr = abilab.abiopen('%s')" % self.filepath),
    #    ])

    #    return self._write_nb_nbpath(nb, nbpath)

    #@staticmethod
    #def _write_nb_nbpath(nb, nbpath):
    #    """
    #    This method must be called at the end of ``write_notebook``.
    #    nb is the jupyter notebook and nbpath the argument passed to ``write_notebook``.
    #    """
    #    import io, os, tempfile
    #    if nbpath is None:
    #        _, nbpath = tempfile.mkstemp(prefix="abinb_", suffix='.ipynb', dir=os.getcwd(), text=True)

    #    # Write notebook
    #    import nbformat
    #    with io.open(nbpath, 'wt', encoding="utf8") as fh:
    #        nbformat.write(nb, fh)
    #        return nbpath

    def get_graphviz(self, engine="automatic", graph_attr=None, node_attr=None, edge_attr=None):
        """
        Generate dependency graph for this Fortran file in the DOT language
        (only all_used_mods and children of this file).

        Args:
            engine: ['dot', 'neato', 'twopi', 'circo', 'fdp', 'sfdp', 'patchwork', 'osage']
            graph_attr: Mapping of (attribute, value) pairs for the graph.
            node_attr: Mapping of (attribute, value) pairs set for all nodes.
            edge_attr: Mapping of (attribute, value) pairs set for all edges.

        Returns: graphviz.Digraph <https://graphviz.readthedocs.io/en/stable/api.html#digraph>
        """
        # https://www.graphviz.org/doc/info/
        from graphviz import Digraph

        graph_attr = {
            'size':'8.90625,1000.0',
            'rankdir': "LR",
            'concentrate':'true',
            #'id':self.ident
        }
        node_attr = {
            'shape':'box',
            'height':'0.0',
            'margin':'0.08',
            'fontname':'Helvetica',
            'fontsize':'10.5'
        }

        edge_attr = {
            'fontname':'Helvetica',
            'fontsize':'9.5'
        }
        graph_attr = {}
        node_attr =  {}
        edge_attr = {}

        fg = Digraph("Abinit project",
            graph_attr=graph_attr,
            node_attr=node_attr,
            edge_attr=edge_attr,
            #format='svg',
            engine="dot" if engine == "automatic" else engine,
        )

        # Set graph attributes.
        #fg.attr(label="%s@%s" % (self.__class__.__name__, self.relworkdir))
        #fg.attr(label=repr(self))
        fg.attr(label=self.name)
        #fg.attr(fontcolor="white", bgcolor='purple:pink')
        fg.attr(rankdir="LR", pagedir="BL")
        #fg.attr(constraint="false", pack="true", packMode="clust")
        fg.node_attr.update(color='lightblue2', style='filled')

        # Add input attributes.
        #if graph_attr is not None: fg.graph_attr.update(**graph_attr)
        #if node_attr is not None: fg.node_attr.update(**node_attr)
        #if edge_attr is not None: fg.edge_attr.update(**edge_attr)

        def node_kwargs(node):
            return node_attr
            return dict(
                #shape="circle",
                #color=node.color_hex,
                #label=(str(node) if not hasattr(node, "pos_str") else
                #    node.pos_str + "\n" + node.__class__.__name__),
            )

        edge_kwargs = dict(arrowType="vee", style="solid")
        cluster_kwargs = dict(rankdir="LR", pagedir="BL", style="rounded", bgcolor="azure2")

        # Build clusters representing directories
        all_nodes = sorted([self] + self.all_used_mods + self.all_usedby_mods, key=lambda o: o.dirname)
        dir2nodes = defaultdict(list)
        for node in all_nodes:
            dir2nodes[node.dirname].append(node)

        for dirname, nodes in dir2nodes.items():
            #print(dirname, nodes)
            cluster_name = "cluster%s" % dirname
            with fg.subgraph(name=cluster_name) as wg:
                wg.graph_attr.update(**graph_attr)
                wg.node_attr.update(**node_attr)
                wg.edge_attr.update(**edge_attr)
                #wg.attr(**cluster_kwargs)
                wg.attr(label=os.path.basename(dirname))
                for node in nodes:
                    wg.node(node.name) #, **node_attr) #, **node_kwargs(child))

        for mod in self.all_used_mods:
            fg.edge(mod.name, self.name) #, label=edge_label, # color=self.color_hex,
        for child in self.all_usedby_mods:
            fg.edge(self.name, child.name) #, label=edge_label, # color=self.color_hex,

        return fg


class AbinitProject(object):
    """
    This object defines the main entry point for client code.
    It contains a dictionary (fort_files) mapping the basename of the Fortran files to FortranFile instances.
    Provides methods to traverse the DAG, print information about the source code
    and generate the configuration files required by the build system.
    """

    DEFAULT_PICKLE_FILE = "_project.pickle"

    IGNORED_FILES = {"m_build_info.F90", "m_optim_dumper.F90"}

    # Marcos used to import modules in abinit libraries.
    # Must be consistent with CPP version. see incs/abi_common.h
    MACROS = {
        # Abinit MACROS.
        "ABI_ASYNC": ",asynchronous",
        "ABI_PRIVATE": ",private",
        "ABI_PROTECTED": ",protected",
        "ABI_CONTIGUOUS": "contigous,",
        # Libpaw.
        "USE_DEFS": "use defs_basis",
        "USE_MPI_WRAPPERS": "use m_xmpi",
        "USE_MSG_HANDLING": "use m_errors, only : msg_hndl, netcdf_check; use m_abicore",
        "USE_MEMORY_PROFILING": "use m_profiling_abi",
        # Libtetra.
    }

    @classmethod
    def pickle_load(cls, filepath=None):
        """
        Reconstruct object from pickle file. Default is used if filepath is None.
        """
        if filepath is None: filepath = cls.DEFAULT_PICKLE_FILE
        with open(filepath, "rb") as fh:
            return pickle.load(fh)

    def __init__(self, srcdir, processes=1, verbose=0):
        # Find directories with abinit.src files inside srcdir
        # and get list of files treated by the build system.
        self.srcdir = os.path.abspath(srcdir)
        self.dirpaths = self.get_dirpaths()
        self.verbose = verbose

        # Get source files from abinit.src and build mapping basename --> FortranFile
        start = time.time()
        def filter_fortran(files):
            return [f for f in files if f.endswith(".f") or f.endswith(".F90")]

        import imp
        name2path = OrderedDict()
        for d in self.dirpaths:
            if os.path.basename(d) == "98_main":
                # Special treatment for programs (no abinit.src here)
                for basename in filter_fortran(os.listdir(d)):
                    if basename in name2path:
                        raise RuntimeError("Found two Fortran files with same basename `%s`" % basename)
                    name2path[basename] = os.path.join(d, basename)
            else:
                # Get source files from abinit.src.
                abinit_src = os.path.join(d, "abinit.src")
                mod = imp.load_source(abinit_src, abinit_src)
                for basename in filter_fortran(mod.sources):
                    if basename in self.IGNORED_FILES: continue
                    if basename in name2path:
                        raise RuntimeError("Found two Fortran files with same basename `%s`" % basename)
                    name2path[basename] = os.path.join(d, basename)

        print("Using %d processes to analyze %d directories with %d files" % (processes, len(self.dirpaths), len(name2path)))

        self.fort_files = OrderedDict()
        if processes == 1:
            for basename, path in name2path.items():
                self.fort_files[basename] = FortranFile.from_path(path, macros=self.MACROS, verbose=self.verbose)
        else:
            from multiprocessing import Pool
            pool = Pool(processes=processes)
            results = pool.map(self._pool_f, list(name2path.items()))
            for basename, fort_file in results:
                self.fort_files[basename] = fort_file

        print("Parsing completed in %.2f [s]" % (time.time() - start))
        self.correlate()

    def _pool_f(self, item):
        #import sys
        #sys.stdout = open(str(os.getpid()) + ".out", "w")
        #sys.stderr = open(str(os.getpid()) + ".err", "w")
        basename, path = item
        return (basename, FortranFile.from_path(path, macros=self.MACROS, verbose=self.verbose))

    def correlate(self):
        print("Building dependency graph ...")
        start = time.time()

        # def correlate()
        # Build dependency graph
        # TODO: check for cyclic dependencies. ?
        for fort_file in self.fort_files.values():
            for use_name in fort_file.all_uses:
                if use_name in EXTERNAL_MODS: continue
                try:
                    used_mod = self.all_modules[use_name]
                except KeyError:
                    raise RuntimeError(("Fortran module `%s` used by `%s` not found in Abinit project.\n" +
                                        "Add it to the EXTERNAL_MODS set if it's not a typo.") % (use_name, fort_file.path))
                fort_file.all_used_mods.append(used_mod)

                # FIXME
                key = os.path.basename(used_mod.path)
                self.fort_files[key].all_usedby_mods.append(fort_file)

        pub_procs = self.all_public_procedures()
        all_interfaces = self.get_all_interfaces()
        miss = []
        for fort_file in self.fort_files.values():
            for proc in fort_file.iter_procedures():
                for child_name in proc.children:
                    try:
                        pub_procs[child_name].parents.append(proc)
                    except KeyError:
                        # TODO: Could be in subroutine contains
                        #if child_name in all_interfaces:
                        #    print("Found in interfaces:", child_name)
                        #else:
                        miss.append(child_name)

        def is_internal(name):
            return not any(name.startswith(s) for s in
                set(("mpi_", "dfftw_", "mkl_", "papif_", "plasma_", "elpa", "etsf_io_", "blacs_",
                     "libpaw_", "gsl_", "gpu_", "xc_", "bigdft_")))

        miss = filter(is_internal, miss)
        from .check_linalg_calls import blas_routines, lapack_routines
        from .regex import FORTRAN_INTRINSICS
        if miss:
            miss = set(miss)
            # Remove blas and lapack routines.
            miss = miss.difference(blas_routines)
            miss = miss.difference(lapack_routines)
            # Remove pblas and scalalapack routines.
            miss = miss.difference("p" + n for n in blas_routines)
            miss = miss.difference("p" + n for n in lapack_routines)
            # Remove public interfaces.
            miss = miss.difference(self.get_all_interfaces().keys())
            # Remove intrincs.
            miss = miss.difference(FORTRAN_INTRINSICS)
            miss = sorted(miss)
            print("Cannot find %d callees. Use --verbose to show list." % len(miss))
            if self.verbose: pprint(miss)

        print("Graph completed in %.2f [s]" % (time.time() - start))

    def __str__(self):
         return self.to_string()

    def to_string(self, verbose=0, width=90):
        """
        String representation with verbosity level `verbose`.
        Text is wrapped at `width` columns.
        """
        lines = []; app = lines.append
        # TODO: Print project stats
        for fort_file in self.fort_files.values():
            app(fort_file.to_string(verbose=verbose, width=width))

        return "\n".join(lines)

    @lazy_property
    def all_modules(self):
        """
        Mapping modules name --> Module object with all modules in project.
        """
        omods = OrderedDict()
        for fort_file in self.fort_files.values():
            for m in fort_file.modules:
                assert m.name not in omods
                omods[m.name] = m
        return omods

    def pickle_dump(self, filepath=None):
        """
        Save the object in pickle format. Default name is used if filepath is None.
        """
        if filepath is None: filepath = self.DEFAULT_PICKLE_FILE
        with open(filepath, "wb") as fh:
            return pickle.dump(self, fh)

    def get_dirpaths(self):
        """
        Return list of directory names with source files.
        """
        l = sorted([d for d in os.listdir(self.srcdir) if os.path.isdir(d) and
                    os.path.isfile(os.path.join(d, "abinit.src"))])

        # 98_main does not have abinit.src so we have to add it here.
        return l + [os.path.join(self.srcdir, "98_main")]

    def needs_reload(self):
        """
        Returns True if source tree must be parsed again because:

            1. new files/directories have been added
            2. source files have been changed
        """
        if set(self.dirpaths) != set(self.get_dirpaths()): return True
        # TODO
        # Return immediately if new files have been added...

        # Compare time of most recent content modification.
        try:
            return any(fort_file.stat.st_mtime != os.stat(fort_file.path).st_mtime
                       for fort_file in self.fort_files.values())
        except FileNotFoundError:
            return True

    def fort_files_indir(self, dirname):
        dir2files = self.groupby_dirname()
        if dirname.endswith(os.sep): dirname = dirname[:-1]
        dirname = os.path.join(self.srcdir, os.path.basename(dirname))
        return dir2files[dirname]

    def groupby_dirname(self):
        """
        Return dictionary mapping dirname --> List of FortranFile.
        """
        dir2files = defaultdict(list)
        for fort_file in self.fort_files.values():
            dir2files[fort_file.dirname].append(fort_file)
        return OrderedDict(dir2files.items())

    def iter_dirname_fortfile(self):
        """Iterate over (dirname, fort_file)"""
        dir2files = self.groupby_dirname()
        for dirname, fort_files in dir2files.items():
            for fort_file in fort_files:
                yield dirname, fort_file

    def all_public_procedures(self):
        """
        Dictionary mapping name --> Procedure
        """
        # FIXME: Check that there's no name collision
        d = {}
        for f in self.fort_files.values():
            for a in ["modules", "programs", "subroutines", "functions"]:
                for p in getattr(f, a):
                    d.update({p.name: p for p in p.public_procedures})
        return d

    def get_all_datatypes(self):
        """
        List with all datatypes available in the project.
        """
        dtypes = []
        for f in self.fort_files.values():
            for p in getattr(f, "modules"):
                dtypes.extend(p.types)
        return dtypes

    def get_all_interfaces(self):
        """
        Dictionary mapping name --> Interface
        """
        d = {}
        for f in self.fort_files.values():
            for p in getattr(f, "modules"):
                d.update({i.name: i for i in p.interfaces})
        return d

    def print_interfaces(self, what=None, verbose=0):
        name2interface = self.get_all_interfaces()
        if what is not None:
            if what in name2interface:
                interface = name2interface[what]
                print(interface.to_string(verbose=verbose))
            else:
                cprint("Cannot find interface `%s` in project" % what, "red")
                matches = difflib.get_close_matches(what, name2interface.keys())
                if matches:
                    cprint("Perhaps you meant: {}".format(matches), "red")
        else:
            for interface in name2interface.values():
                cprint(repr(interface), "yellow")
                print(interface.to_string(verbose=verbose))
                print("")

    def find_public_entity(self, name):
        """
        Find and return Procedure object with name `name`.
        Assume name is unique in the project.
        """
        all_names = []
        for f in self.fort_files.values():
            obj = f.find_public_entity(name, all_names=all_names)
            if obj is not None: return obj

        # Print closest matches
        cprint("Cannot find public entity `%s`" % str(name), "red")
        matches = difflib.get_close_matches(name, all_names)
        if matches:
            cprint("Perhaps you meant: {}".format(matches), "red")

        return None

    def find_datatype(self, name):
        """
        Find and return Datatype object with name `name`.
        Assume name is unique in the project.
        """
        all_names = []
        for f in self.fort_files.values():
            obj = f.find_datatype(name, all_names=all_names)
            if obj is not None: return obj

        # Print closest matches
        matches = difflib.get_close_matches(name, all_names)
        if matches: cprint("Perhaps you meant: {}".format(matches), "red")

        return None

    def find_module_from_entity(self, name):
        """
        Return the Module object that contains the public entity `name`.
        """
        obj = self.find_public_entity(name)
        if obj.is_module: return obj
        assert obj.ancestor is not None and obj.ancestor.is_module
        return obj.ancestor

    def print_dir(self, dirname, verbose=0):
        #dirname = os.path.basename(dirname).replace(os.sep, "")
        #dirname = dirname.replace(os.sep, "")
        #files = [f for f in self.fort_files.values() if os.path.basename(f.dirname) == dirname]
        dir2files = self.groupby_dirname()
        if dirname.endswith(os.sep): dirname = dirname[:-1]
        print("Printing directory:", dirname)
        dirname = os.path.join(self.srcdir, os.path.basename(dirname))

        if verbose:
            for f in dir2files[dirname]:
                print(f.to_string(verbose=verbose, with_dataframe=False))
                print("")

        df = self.get_stats_dir(dirname)
        print(df)

    #def find_parents(self, obj):
    #    """
    #    Find parents of object `obj` where obj can be a file or the name of a procedure.
    #    """
    #    parents = []
    #    print("Finding parents of", obj)
    #    if os.path.isfile(obj):
    #        # Assume module with same name as F90 file
    #        this = FortranFile(obj)
    #        for fort_file in self.fort_files.values():
    #            if this in fort_file.parents:
    #                print(fort_file.basename)
    #                #parents.append(fort_file)
    #    else:
    #        raise NotImplementedError()

    #    return parents

    #def detect_cyclic_deps(self):
    #    cycles = defaultdict(list)
    #    for fort_file in self.fort_files.values():
    #        # TODO: This should be recursive
    #        for child in fort_file.all_usedby_mods:
    #            if child.depends_on(fort_file):
    #                cycles[fort_file].append(child)
    #    return cycles

    def check_dirlevel(self):
        errors = []
        for fort_file in self.fort_files.values():
            for child in fort_file.all_usedby_mods:
                if child.dirlevel < fort_file.dirlevel:
                    errors.append("%s should be below level: %s" % (repr(child), fort_file.dirlevel))
        return "\n".join(errors)

    def find_allmods(self, head_path):
        """
        Traverse the *entire* graph starting from head_path.
        Return full list of `Module` objects required by head_path.
        """
        head = self.fort_files[head_path]
        allmods, queue, visited = set(), set(), set()
        queue.add(head)
        while queue:
            fort_file = queue.pop()
            for mod in fort_file.all_used_mods:
                if mod.basename in visited: continue
                visited.add(mod.basename)
                allmods.add(mod)
                queue.add(self.fort_files[mod.basename])

        return allmods

    def write_binaries_conf(self, dryrun=False, verbose=0):
        """
        Write new binaries.conf file
        """
        # Read binaries.conf and add new list of libraries.
        # NB: To treat `dependencies` in an automatic way, I would need either an
        # explicit "use external_module" or an explicit "include foo.h" so
        # that I can map these names to external libraries.
        # This means that I **cannot generate** the entire file in a programmatic way
        # but only the libraries entries.
        try:
            from ConfigParser import ConfigParser
        except ImportError:
            from configparser import ConfigParser

        binconf_path = os.path.join(self.srcdir, "..", "config", "specs", "binaries.conf")

        # Read INI file.
        config = ConfigParser()
        config.read(binconf_path)

        # Read header with comments to be added to the new conf file.
        # NB: use [DEFAULT] as sentinel.
        header = []
        with open(binconf_path, "rt") as fh:
            for line in fh:
                line = line.strip()
                if line == "[DEFAULT]":
                    break
                else:
                    header.append(line)
            else:
                raise ValueError("Cannot find `[DEFAULT]` string in %s" % binconf_path)
        header.append("")
        header = "\n".join(header)

        print("Finding all binary dependencies...")
        start = time.time()
        # Find programs
        program_paths = []
        for path, fort_file in self.fort_files.items():
            if fort_file.programs:
                program_paths.append((fort_file, path))

        for prog_file, path in program_paths:
            allmods = self.find_allmods(path)
            dirnames = sorted(set(mod.dirname for mod in allmods), reverse=True)
            if verbose:
                print("For program:", prog_file.name)
                pprint(dirnames)

            prog_name = prog_file.programs[0].name
            if prog_name.lower() == "fold2bloch": prog_name = "fold2Bloch"
            config.set(prog_name, "libraries", "\n" + "\n".join(dirnames))
            # py3k
            #config[prog_name]["libraries"] = "\n" + "\n".join(dirnames)

        print("Analysis completed in %.2f [s]" % (time.time() - start))

        try:
            from StringIO import StringIO
        except ImportError:
            from io import StringIO

        fobj = StringIO()
        fobj.write(header)
        config.write(fobj)
        if dryrun:
            print(fobj.getvalue())
        else:
            #shutil.copyfile(binconf_path, binconf_path + ".bkp")
            with open(binconf_path, "wt") as fh:
                fh.write(fobj.getvalue())
        fobj.close()

        return 0

    def write_buildsys_files(self, dryrun=False, verbose=0):
        """
        Write files require by buildsys:

            abinit.dep --> Dependencies inside the directory
            abinit.dir --> Dependencies outside the directory
            abinit.amf --> File with EXTRA_DIST
        """
        dir2files = self.groupby_dirname()
        # Sort...

        template = """\
# Dependencies ({kind}) of directory {directory}
#
# This file has been generated by abisrc.py.
# DO NOT edit this file. All changes will be lost.
# Use `abisrc.py makemake` to regenerate the file.

"""
        for dirpath, fort_files in dir2files.items():
            dirname = os.path.basename(dirpath)
            if dirname == "98_main": continue

            # Find dependencies inside this directory (abinit.dep)
            inside_deps = {}
            for this in fort_files:
                dlist = [f.name for f in fort_files if any(m in this.all_used_mods for m in f.modules)]
                inside_deps[this.name] = sorted(set(d.replace(".F90", "") for d in dlist))
            inside_deps = OrderedDict([(k, inside_deps[k]) for k in sorted(inside_deps.keys())])

            lines = []
            cleanfiles = ["CLEANFILES += \\"]
            n = len(inside_deps)
            for i, (k, dlist) in enumerate(inside_deps.items()):
                k = k.replace(".F90", "")
                end = " " if i == n - 1 else " \\"
                cleanfiles.append("\t%s.$(MODEXT)%s" % (k, end))
                if not dlist: continue
                lines.append("%s.$(OBJEXT): %s " % (k, " ".join("%s.$(OBJEXT)" % v for v in dlist)))
            cleanfiles.append("\n")

            # Write abinit.dep
            s = template.format(kind="inside the directory", directory=dirname)
            s += "\n".join(cleanfiles)
            s += "\n\n".join(lines)

            abinitdep_path = os.path.join(dirpath, "abinit.dep")
            if dryrun:
                print("# For dirpath:", dirpath)
                print(s, end=2 * "\n")
            else:
                #shutil.copyfile(abinitdep_path, abinitdep_path + ".bkp")
                with open(abinitdep_path, "wt") as fh:
                    fh.write(s)

            # Find dependencies outside this directory (abinit.dir).
            outside_dir = []
            for fort_file in fort_files:
                outside_dir.extend(m.dirname for m in fort_file.all_used_mods)

	    # Write abinit.dir
            s = template.format(kind="outside the directory", directory=os.path.basename(dirpath))
            s += "include_dirs = \\\n" + pformat(sorted(set(outside_dir)))
            abinitdir_path = os.path.join(dirpath, "abinit.dir")
            if dryrun:
                print(s, end=2 * "\n")
            else:
                #if os.path.exists(abinitdir_path):
                #    shutil.copyfile(abinitdir_path, abinitdir_path + ".bkp")
                with open(abinitdir_path, "wt") as fh:
                    fh.write(s)

    def touch_alldeps(self, verbose=0):
        """
        Touch all files that depend on the modules that have been changed.
        Return number of touched files.
        """
        def touch(fname, times=None):
            """Emulate Unix touch."""
            with open(fname, 'a'):
                os.utime(fname, times)

        # TODO: possible problem if new files have been added.
        count = 0
        changed_fort_files = []
        for fort_file in self.fort_files.values():
            if fort_file.stat.st_mtime != os.stat(fort_file.path).st_mtime:
                changed_fort_files.append(fort_file)
                print("Detected changes in:", os.path.relpath(fort_file.path))
                touch(fort_file.path)
                count += 1

        # Get list of modules that have been changed.
        changed_mods = []
        for f in changed_fort_files:
            changed_mods.extend(f.modules)

        for fort_file in self.fort_files.values():
            if any(m in fort_file.all_used_mods for m in changed_mods):
                print("Touching:", os.path.relpath(fort_file.path))
                touch(fort_file.path)
                count += 1

        return count

    #def canimove_file(self, filename, dest_level):
    #    fort_file = self.fort_files[os.path.basename(filename)]

    #def canimove_dir(self, dirname, dest_level):
    #    dir2files = sel.groupby_dirname()
    #    dirpath = os.path.join(self.srcdir, dirname)
    #    if dirpath.endswith(os.sep): dirpath = dirpath[:-1]
    #    for fort_file in dir2files[dirpath]:

    def validate(self, verbose=0):
        """
        Validate project. Return exit status.
        """
        white_list = set(["m_psxml2ab.F90",])

        retcode = 0
        for fort_file in self.fort_files.values():
            count = len(fort_file.subroutines) + len(fort_file.functions)
            if count == 0: continue
            if fort_file.name in white_list:
                cprint("WHITE_LIST [%s] Found %d procedure(s) outside modules!" % (fort_file.name, count), "green")
            else:
                cprint("[%s] Found %d procedure(s) outside modules!" % (fort_file.name, count), "red")
                retcode += 1

        errstr = self.check_dirlevel()
        if errstr:
            cprint(errstr, "red")
            retcode += 1

        cprint("retcode %d" % retcode, "green" if retcode == 0 else "red")
        return retcode

    def check_abirules(self, verbose=0):
        retcode = 0
        for name, fort_file in self.fort_files.items():
            print("Checking abirules for %s..." % name)
            rv = fort_file.check_abirules(verbose=verbose)
            retcode += rv
            msg = "%s [OK]" if rv == 0 else "%s [FAILED]"
            cprint(msg % name, color="green" if rv == 0 else "red")

        return retcode

    def pedit(self, name, verbose=0):
        """
        Edit all children of a public entity specified by name.
        """
        obj = self.find_public_entity(name)
        if obj is None: return 1
        if verbose: print(obj)

        # Find files with procedures.
        paths = sorted(set(p.path for p in obj.parents))
        if verbose: print(paths)
        # TODO
        #from pymods.tools import Editor
        from fkiss.tools import Editor
        return Editor().edit_files(paths, ask_for_exit=True)

    def get_stats_file(self, filename, as_dataframe=True):
        return self.fort_files[os.path.basename(filename)].get_stats(as_dataframe=as_dataframe)

    def get_stats_dir(self, dirname):
        dir2files = self.groupby_dirname()
        dirpath = os.path.join(self.srcdir, dirname)
        if dirpath.endswith(os.sep): dirpath = dirpath[:-1]
        index, rows = [], []
        for fort_file in sorted(dir2files[dirpath], key=lambda f: f.basename):
            index.append(fort_file.basename)
            rows.append(fort_file.get_stats())

        import pandas as pd
        return pd.DataFrame(rows, index=index, columns=list(rows[0].keys() if rows else None))

    def get_stats(self):
        df_list = []
        for dirpath in self.dirpaths:
            dirname = os.path.basename(dirpath)
            try:
                df = self.get_stats_dir(dirname)
                df_list.append(df)
            except Exception as exc:
                print("exception for dirname: %s\n%s" % (dirname, exc))
                #raise exc

        import pandas as pd
        return pd.concat(df_list)

    def print_orphans(self, verbose=0):
        """Print orphan procedures and modules."""
        # FIXME: this does not work as expected.

        def find_orphans_in_fort_file(fort_file):
            """List with orphan procedures."""
            orphans = []
            for mod in fort_file.modules:
                if all(mod.name not in ffile.all_uses for ffile in self.fort_files.values()):
                    orphans.append(mod)

            for proc in fort_file.iter_procedures(visibility="public"):
                # programs are orphan by definition
                # function callers are difficult to detect.
                if proc.is_program or proc.is_function: continue
                if not proc.parents:
                    orphans.append(proc)

            return orphans

        dir2files = self.groupby_dirname()
        for _, fort_file in self.iter_dirname_fortfile():
            orphans = find_orphans_in_fort_file(fort_file)
            if orphans:
                cprint("Found %d orphans in file: %s" % (len(orphans), fort_file.basename), "yellow")
                for o in orphans:
                    print("\t", repr(o))

    def get_graphviz_dir(self, dirname, engine="automatic", graph_attr=None, node_attr=None, edge_attr=None):
        """
        Generate dependency graph for directory `dirname` in the DOT language

        Args:
            engine: ['dot', 'neato', 'twopi', 'circo', 'fdp', 'sfdp', 'patchwork', 'osage']
            graph_attr: Mapping of (attribute, value) pairs for the graph.
            node_attr: Mapping of (attribute, value) pairs set for all nodes.
            edge_attr: Mapping of (attribute, value) pairs set for all edges.

        Returns: graphviz.Digraph <https://graphviz.readthedocs.io/en/stable/api.html#digraph>
        """
        dir2files = self.groupby_dirname()
        dirname = os.path.join(self.srcdir, os.path.basename(dirname))

        parent_dirs, child_dirs = [], []
        for fort_file in dir2files[dirname]:
            for use_name in fort_file.all_uses:
                if use_name in EXTERNAL_MODS: continue
                used_mod = self.all_modules[use_name]
                parent_dirs.append(os.path.basename(used_mod.dirname))

        parent_dirs = sorted(set(parent_dirs))
        print(parent_dirs)

        # https://www.graphviz.org/doc/info/
        #from graphviz import Digraph
        #fg = Digraph("Directory: %s" % dirname, engine="dot" if engine == "automatic" else engine)
        #return fg

    def get_graphviz_pubname(self, name, engine="automatic", graph_attr=None, node_attr=None, edge_attr=None):
        """
        Generate dependency graph for public procedure `name` in the DOT language
        (only parents and children modules of this file).

        Args:
            engine: ['dot', 'neato', 'twopi', 'circo', 'fdp', 'sfdp', 'patchwork', 'osage']
            graph_attr: Mapping of (attribute, value) pairs for the graph.
            node_attr: Mapping of (attribute, value) pairs set for all nodes.
            edge_attr: Mapping of (attribute, value) pairs set for all edges.

        Returns: graphviz.Digraph <https://graphviz.readthedocs.io/en/stable/api.html#digraph>
        """
        obj = self.find_public_entity(name)
        if obj is None: return None

        print(obj)
        cprint("graphvis for public entities not yet available", "yellow")
        obj.parents
        obj.children
        #return obj.get_graphviz(engine=engine, graph_attr=graph_attr, node_attr=node_attr, edge_attr=edge_attr)

    def master(self):
        return """\

Master Foo and the Hardware Designer

On one occasion, as Master Foo was traveling to a conference
with a few of his senior disciples, he was accosted by a hardware designer.

The hardware designer said:
“It is rumored that you are a great programmer. How many lines of code do you write per year?”

Master Foo replied with a question:
“How many square inches of silicon do you lay out per year?”

“Why...we hardware designers never measure our work in that way,” the man said.

“And why not?” Master Foo inquired.

“If we did so,” the hardware designer replied, “we would be tempted to design chips
so large that they cannot be fabricated - and, if they were fabricated,
their overwhelming complexity would make it be impossible to generate proper test vectors for them.”

Master Foo smiled, and bowed to the hardware designer.

In that moment, the hardware designer achieved enlightenment.

From http://www.catb.org/esr/writings/unix-koans/
"""

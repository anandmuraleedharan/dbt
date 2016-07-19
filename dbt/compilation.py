
import os
import fnmatch
import jinja2
from collections import defaultdict
import dbt.project
from dbt.source import Source

import networkx as nx

class Linker(object):
    def __init__(self):
        self.graph = nx.DiGraph()

    def nodes(self):
        return self.graph.nodes()

    def as_dependency_list(self, limit_to=None):
        return nx.topological_sort(self.graph, nbunch=limit_to)

    def dependency(self, node1, node2):
        "indicate that node1 depends on node2"
        self.graph.add_node(node1)
        self.graph.add_node(node2)
        self.graph.add_edge(node2, node1)

    def add_node(self, node):
        self.graph.add_node(node)

    def write_graph(self, outfile):
        nx.write_yaml(self.graph, outfile)

    def read_graph(self, infile):
        self.graph = nx.read_yaml(infile)

class Compiler(object):
    def __init__(self, project, create_template_class):
        self.project = project
        self.create_template = create_template_class()

    def initialize(self):
        if not os.path.exists(self.project['target-path']):
            os.makedirs(self.project['target-path'])

        if not os.path.exists(self.project['modules-path']):
            os.makedirs(self.project['modules-path'])

    def dependency_projects(self):
        for obj in os.listdir(self.project['modules-path']):
            full_obj = os.path.join(self.project['modules-path'], obj)
            if os.path.isdir(full_obj):
                project = dbt.project.read_project(os.path.join(full_obj, 'dbt_project.yml'))
                yield project


    def model_sources(self, project):
        "source_key is a dbt config key like source-paths or analysis-paths"
        paths = project.get('source-paths', [])
        return Source(project).get_models(paths)

    def analysis_sources(self, project):
        "source_key is a dbt config key like source-paths or analysis-paths"
        paths = project.get('analysis-paths', [])
        return Source(project).get_analyses(paths)

    def validate_models_unique(self, models):
        model_names = set()
        for model in models:
            if model.name in model_names:
                # TODO : Package?
                raise RuntimeError("ERROR: Conflicting model found model={}".format(model.name))
            else:
                model_names.add(model.name)

    def __write(self, build_filepath, payload):
        target_path = os.path.join(self.project['target-path'], build_filepath)

        if not os.path.exists(os.path.dirname(target_path)):
            os.makedirs(os.path.dirname(target_path))

        print target_path

        with open(target_path, 'w') as f:
            f.write(payload)

    def __get_model_identifiers(self, model_filepath):
        model_group = os.path.dirname(model_filepath)
        model_name, _ = os.path.splitext(os.path.basename(model_filepath))
        return model_group, model_name

    def find_model_by_name(self, project_models, name, package_namespace=None):
        found = []
        for model in project_models:
            if model.name == name:
                if package_namespace is None:
                    found.append(model)
                elif package_namespace is not None and package_namespace == model.project['name']:
                    found.append(model)

        nice_package_name = 'ANY' if package_namespace is None else package_namespace
        if len(found) == 0:
            raise RuntimeError("Can't find a model named '{}' in package '{}' -- does it exist?".format(name, nice_package_name))
        elif len(found) == 1:
            return found[0]
        else:
            raise RuntimeError("Model specification is ambiguous: model='{}' package='{}' -- {} models match criteria: {}".format(name, nice_package_name, len(found), found))

    def __ref(self, linker, ctx, model, all_models):
        schema = ctx['env']['schema']

        # if this node doesn't have any deps, still make sure it's a part of the graph
        source_model = tuple(model.fqn)
        linker.add_node(source_model)

        def do_ref(*args):
            if len(args) == 1:
                other_model_name = args[0]
                other_model = self.find_model_by_name(all_models, other_model_name)
            elif len(args) == 2:
                other_model_package, other_model_name = args
                other_model = self.find_model_by_name(all_models, other_model_name, package_namespace=other_model_package)

            other_model_name = self.create_template.model_name(other_model_name)

            # TODO : wtf is up here?
            other_model_fqn = tuple(other_model.fqn[:-1] + [other_model_name])

            linker.dependency(source_model, other_model_fqn)
            return '"{}"."{}"'.format(schema, other_model_name)

        return do_ref

    def compile_model(self, linker, model, models):
        jinja = jinja2.Environment(loader=jinja2.FileSystemLoader(searchpath=model.top_dir))

        model_config = model.get_config(self.project)

        if not model_config.get('enabled'):
            return None

        template = jinja.get_template(model.rel_filepath)

        context = self.project.context()
        context['ref'] = self.__ref(linker, context, model, models)

        rendered = template.render(context)

        stmt = model.compile(rendered, self.project, self.create_template)
        if stmt:
            build_path = model.build_path(self.create_template)
            self.__write(build_path, stmt)
            return True
        return False

    def __write_graph_file(self, linker):
        filename = 'graph-{}.yml'.format(self.create_template.label)
        graph_path = os.path.join(self.project['target-path'], filename)
        linker.write_graph(graph_path)

    def compile(self):
        models = self.model_sources(self.project)

        for project in self.dependency_projects():
            models.extend(self.model_sources(project))

        self.validate_models_unique(models)

        model_linker = Linker()
        compiled_models = []
        for model in models:
            compiled = self.compile_model(model_linker, model, models)
            if compiled:
                compiled_models.append(compiled)

        self.__write_graph_file(model_linker)

        analysis_linker = Linker()
        analyses = self.analysis_sources(self.project)
        compiled_analyses = []
        for analysis in analyses:
            compiled = self.compile_model(analysis_linker, analysis, models)
            if compiled:
                compiled_analyses.append(compiled)

        return len(compiled_models), len(compiled_analyses)


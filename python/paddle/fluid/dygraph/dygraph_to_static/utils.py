# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function

import inspect
import gast
import astor
import atexit
import os
import tempfile
import six
import imp

dygraph_class_to_static_api = {
    "BatchNorm": "batch_norm",
    "BilinearTensorProduct": "bilinear_tensor_product",
    "Conv2D": "conv2d",
    "Conv3D": "conv3d",
    "Conv2DTranspose": "conv2d_transpose",
    "Conv3DTranspose": "conv3d_transpose",
    "CosineDecay": "cosine_decay",
    "Embedding": "embedding",
    "ExponentialDecay": "exponential_decay",
    "GroupNorm": "group_norm",
    "GRUUnit": "gru_unit",
    "InverseTimeDecay": "inverse_time_decay",
    "LayerNorm": "layer_norm",
    "Linear": "fc",
    "NaturalExpDecay": "natural_exp_decay",
    "NCE": "nce",
    "NoamDecay": "noam_decay",
    "PiecewiseDecay": "piecewise_decay",
    "PolynomialDecay": "polynomial_decay",
    "Pool2D": "pool2d",
    "PRelu": "prelu",
    "SpectralNorm": "spectral_norm",
}


def _delete_keywords_from(node):
    assert isinstance(node, gast.Call)
    func_src = astor.to_source(node.func)
    import paddle.fluid as fluid
    full_args = eval("inspect.getargspec({})".format(func_src))
    full_args_name = full_args[0]

    node.keywords = [k for k in node.keywords if k.arg in full_args_name]
    return


def to_static_api(dygraph_class):
    if dygraph_class in dygraph_class_to_static_api:
        return dygraph_class_to_static_api[dygraph_class]
    else:
        raise NotImplementedError("Paddle dygraph API {} cannot be converted "
                                  "to static graph at present.".format(
                                      dygraph_class))


def _add_keywords_to(node, dygraph_api_name):
    assert isinstance(node, gast.Call)
    if dygraph_api_name == "Linear":
        for ast_keyword in node.keywords:
            if ast_keyword.arg == "output_dim":
                ast_keyword.arg = "size"

        node.keywords.append(
            gast.keyword(
                arg="num_flatten_dims",
                value=gast.Constant(
                    value=-1, kind=None)))

    if dygraph_api_name == "BilinearTensorProduct":
        for ast_keyword in node.keywords:
            if ast_keyword.arg == "output_dim":
                ast_keyword.arg = "size"

    if dygraph_api_name == "PRelu":
        for ast_keyword in node.keywords:
            if ast_keyword.arg == "input":
                ast_keyword.arg = "x"
    return


def _is_paddle_dygraph_api(obj):
    m = inspect.getmodule(obj)
    return m is not None and m.__name__.startswith("paddle.fluid.dygraph")


def is_dygraph_api(node):
    assert isinstance(node, gast.Call)
    func_src = astor.to_source(node.func)
    try:
        import paddle.fluid as fluid
        return eval("_is_paddle_dygraph_api({})".format(func_src))
    except NameError:
        return False


def is_to_variable(node):
    assert isinstance(node, gast.Call)
    if is_dygraph_api(node):
        api_name = node.func.attr
        return api_name == "to_variable"
    return False


def to_static_ast(node, class_node):
    assert isinstance(node, gast.Call)
    assert isinstance(class_node, gast.Call)
    static_api = to_static_api(class_node.func.attr)

    node.func = gast.Attribute(
        attr=static_api,
        ctx=gast.Load(),
        value=gast.Attribute(
            attr='layers',
            ctx=gast.Load(),
            value=gast.Name(
                ctx=gast.Load(), id='fluid', annotation=None,
                type_comment=None)))

    update_args_of_func(node, class_node, 'forward')

    node.args.extend(class_node.args)
    node.keywords.extend(class_node.keywords)
    _add_keywords_to(node, class_node.func.attr)
    _delete_keywords_from(node)

    gast.fix_missing_locations(node)

    return node


def to_assign_node(ori_node):
    assert isinstance(ori_node, gast.Call)
    assign_api = gast.parse('fluid.layers.assign').body[0].value
    ori_node.func = assign_api
    return ori_node


def update_args_of_func(node, dygraph_node, method_name):
    assert isinstance(node, gast.Call)
    if method_name not in ["__init__", "forward"]:
        raise ValueError(
            "The method name of class to update args should be '__init__' or 'forward'"
        )

    class_src = astor.to_source(dygraph_node.func)
    import paddle.fluid as fluid
    if method_name == "__init__" or eval(
            "issubclass({}, fluid.dygraph.Layer)".format(class_src)):
        full_args = eval("inspect.getargspec({}.{})".format(class_src,
                                                            method_name))
        full_args_name = [
            arg_name for arg_name in full_args[0] if arg_name != "self"
        ]
    else:
        full_args_name = []
    added_keywords = []
    for idx, arg in enumerate(node.args):
        added_keywords.append(gast.keyword(arg=full_args_name[idx], value=arg))

    node.args = []
    node.keywords = added_keywords + node.keywords

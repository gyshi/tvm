# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
import tvm
from tvm import relay

def test_fuse_simple():
    """Simple testcase."""
    def before():
        x = relay.var("x", shape=(10, 20))
        y = relay.add(x, relay.const(1, "float32"))
        z = relay.exp(y)
        w = relay.squeeze(z)
        return relay.Function([x], w)

    def expected():
        x = relay.var("p", shape=(10, 20))
        y = relay.add(x, relay.const(1, "float32"))
        z = relay.exp(y)
        w = relay.squeeze(z)
        f1 = relay.Function([x], w)
        x = relay.var("x", shape=(10, 20))
        y = relay.Call(f1, [x])
        return relay.Function([x], y)

    z = before()
    z = relay.ir_pass.infer_type(z)
    zz = relay.ir_pass.fuse_ops(z, opt_level=2)
    zz = relay.ir_pass.infer_type(zz)
    zz = relay.ir_pass.fuse_ops(zz)
    zz = relay.ir_pass.infer_type(zz)
    after = relay.ir_pass.infer_type(expected())
    assert relay.ir_pass.alpha_equal(zz, after)


def test_conv2d_fuse():
    """Test fusion case of conv2d"""
    def before(dshape):
        x = relay.var("x", shape=dshape)
        x = relay.add(x, relay.const(1, "float32"))
        y = relay.nn.conv2d(x, relay.var("w1"),
                            kernel_size=(3, 3),
                            padding=(1, 1),
                            channels=16)
        # this is the next dominator.
        y1 = relay.add(relay.const(1, "float32"), y)
        y = relay.add(y, y1)
        # second path
        z2 = relay.nn.conv2d(y, relay.var("w2"),
                             kernel_size=(1, 1),
                             padding=(0,0),
                             channels=16)
        z3 = relay.nn.conv2d(y, relay.var("w3"),
                             kernel_size=(3, 3),
                             padding=(1,1),
                             channels=16)
        # add can only be fused to z1
        z = relay.add(z2, z3)
        return relay.Function(relay.ir_pass.free_vars(z), z)

    def expected(dshape):
        # segment 0
        x = relay.var("p0", shape=dshape)
        y = relay.add(x, relay.const(1, "float32"))
        f0 = relay.Function([x], y)
        # segment 1
        x = relay.var("p0", shape=dshape)
        w = relay.var("p1")
        y = relay.nn.conv2d(x, w,
                            kernel_size=(3, 3),
                            padding=(1, 1),
                            channels=16)
        y1 = relay.add(relay.const(1, "float32"), y)
        y = relay.add(y, y1)
        f1 = relay.Function([x, w], y)
        # segment 2
        x = relay.var("p0", shape=dshape)
        w = relay.var("p1")
        z2 = relay.nn.conv2d(x, w,
                             kernel_size=(3, 3),
                             padding=(1,1),
                             channels=16)
        f2 = relay.Function([x, w], z2)
        # segment 3
        x = relay.var("p0", shape=dshape)
        w = relay.var("p1")
        offset = relay.var("p2", shape=dshape)
        z3 = relay.nn.conv2d(x, w,
                             kernel_size=(1, 1),
                             padding=(0, 0),
                             channels=16)
        z3 = relay.add(z3, offset)
        f3 = relay.Function([x, w, offset], z3)
        # compose
        x = relay.var("x", shape=dshape)
        y = relay.Call(f0, [x])
        y = relay.Call(f1, [y, relay.var("w1")])
        z2 = relay.Call(f2, [y, relay.var("w3")])
        z3 = relay.Call(f3, [y, relay.var("w2"), z2])
        z = z3
        return relay.Function(relay.ir_pass.free_vars(z), z)

    dshape = (1, 16, 64, 64)
    z = before(dshape)
    z = relay.ir_pass.infer_type(z)
    zz = relay.ir_pass.fuse_ops(z, opt_level=2)
    zz = relay.ir_pass.infer_type(zz)
    after = relay.ir_pass.infer_type(expected(dshape))
    assert relay.ir_pass.alpha_equal(zz, after)


def test_concatenate():
    """Test fusion case involving concat op and Tuple node"""

    def before(dshape):
        x = relay.var("x", shape=dshape)
        pooled = relay.nn.max_pool2d(x, pool_size=(2, 2), strides=(2, 2), padding=(0, 0))
        upsampled = relay.nn.upsampling(pooled, scale=2, layout="NCHW")
        concat = relay.concatenate((upsampled, x), axis=1)
        out = relay.add(concat, relay.const(1, "float32"))
        return relay.Function(relay.ir_pass.free_vars(out), out)

    def expected(dshape):
        x = relay.var("x", shape=dshape)
        pooled = relay.nn.max_pool2d(x, pool_size=(2, 2), strides=(2, 2), padding=(0, 0))
        f0 = relay.Function([x], pooled)

        p0 = relay.var("p0", shape=(dshape[0], dshape[1], dshape[2]//2, dshape[3]//2))
        p1 = relay.var("p1", shape=dshape)
        upsampled = relay.nn.upsampling(p0, scale=2, layout="NCHW")
        concat = relay.concatenate((upsampled, p1), axis=1)
        out = relay.add(concat, relay.const(1, "float32"))
        f1 = relay.Function([p0, p1], out)

        x = relay.var("x", shape=dshape)
        y = relay.Call(f0, [x])
        z = relay.Call(f1, [y, x])
        return relay.Function([x], z)

    dshape = (1, 16, 64, 64)
    z = before(dshape)
    z = relay.ir_pass.infer_type(z)
    zz = relay.ir_pass.fuse_ops(z, opt_level=0)
    assert not relay.ir_pass.free_vars(zz)
    zz = relay.ir_pass.fuse_ops(z, opt_level=2)
    zz = relay.ir_pass.infer_type(zz)
    assert not relay.ir_pass.free_vars(zz)
    after = relay.ir_pass.infer_type(expected(dshape))
    assert relay.ir_pass.alpha_equal(zz, after)


def test_tuple_root():
    """Test fusion case where Tuple node is the root in its group"""

    def before(dshape):
        x = relay.var("x", shape=dshape)
        pooled = relay.nn.max_pool2d(x, pool_size=(2, 2), strides=(2, 2), padding=(0, 0))
        upsampled = relay.nn.upsampling(pooled, scale=2, layout="NCHW")
        out = relay.Tuple((upsampled, x))
        return relay.Function(relay.ir_pass.free_vars(out), out)

    def expected(dshape):
        x = relay.var("x", shape=dshape)
        pooled = relay.nn.max_pool2d(x, pool_size=(2, 2), strides=(2, 2), padding=(0, 0))
        f0 = relay.Function([x], pooled)

        p0 = relay.var("p0", shape=(dshape[0], dshape[1], dshape[2]//2, dshape[3]//2))
        upsampled = relay.nn.upsampling(p0, scale=2, layout="NCHW")
        f1 = relay.Function([p0], upsampled)

        x = relay.var("x", shape=dshape)
        y = relay.Call(f0, [x])
        z = relay.Call(f1, [y])
        tup = relay.Tuple((z, x))
        return relay.Function([x], tup)

    dshape = (1, 16, 64, 64)
    z = before(dshape)
    z = relay.ir_pass.infer_type(z)
    zz = relay.ir_pass.fuse_ops(z, opt_level=0)
    assert not relay.ir_pass.free_vars(zz)
    zz = relay.ir_pass.fuse_ops(z, opt_level=2)
    zz = relay.ir_pass.infer_type(zz)
    assert not relay.ir_pass.free_vars(zz)
    after = relay.ir_pass.infer_type(expected(dshape))
    assert relay.ir_pass.alpha_equal(zz, after)



def test_stop_fusion():
    def before(dshape):
        x = relay.var("x", shape=dshape)
        y = relay.add(x, relay.const(1, "float32"))
        y = relay.annotation.stop_fusion(y)
        z = relay.exp(y)
        return relay.Function([x], z)

    def expected(dshape):
        x = relay.var("p0", shape=dshape)
        y = relay.add(x, relay.const(1, "float32"))
        f1 = relay.Function([x], y)

        x = relay.var("p01", shape=dshape)
        y = relay.exp(x)
        f2 = relay.Function([x], y)

        x = relay.var("x", shape=dshape)
        y = relay.Call(f1, [x])
        z = relay.Call(f2, [y])
        return relay.Function([x], z)

    dshape = (10, 20)
    z = before(dshape)
    z = relay.ir_pass.infer_type(z)
    z = relay.ir_pass.fuse_ops(z)
    z = relay.ir_pass.infer_type(z)
    after = relay.ir_pass.infer_type(expected(dshape))
    assert relay.ir_pass.alpha_equal(z, after)


def test_fuse_myia_regression():
    def before(dshape, dtype):
        x = relay.var('x', shape=dshape, dtype=dtype)
        y = relay.var('y', shape=dshape, dtype=dtype)
        sb = relay.ScopeBuilder()
        with sb.if_scope(relay.op.greater(x, y)):
            sb.ret(relay.Function([], x))
        with sb.else_scope():
            sb.ret(relay.Function([], y))
        return relay.Function([x, y],
            relay.Call(sb.get(), []))

    def expected(dshape, dtype):
        x = relay.var('x', shape=dshape, dtype=dtype)
        y = relay.var('y', shape=dshape, dtype=dtype)
        sb = relay.ScopeBuilder()
        p1 = relay.var('p1', shape=dshape, dtype=dtype)
        p2 = relay.var('p2', shape=dshape, dtype=dtype)
        fused_gt = relay.Function([p1, p2],
            relay.op.greater(p1, p2))
        with sb.if_scope(fused_gt(x, y)):
            sb.ret(relay.Function([], x))
        with sb.else_scope():
            sb.ret(relay.Function([], y))
        return relay.Function([x, y],
            relay.Call(sb.get(), []))

    dshape = ()
    dtype = 'int64'
    f = before(dshape, dtype)
    f = relay.ir_pass.infer_type(f)
    f = relay.ir_pass.fuse_ops(f)
    after = relay.ir_pass.infer_type(expected(dshape, dtype))
    assert relay.ir_pass.alpha_equal(f, after)


def test_fuse_tuple_get_elemwise():
    def before(dim):
        X = relay.var("X", shape=(1, dim))
        W = relay.var("W", shape=(3 * dim, dim))
        matmul = relay.nn.dense(X, W)
        splitted = relay.split(matmul, indices_or_sections=3, axis=1)
        out = relay.sigmoid(splitted[0]) + relay.tanh(splitted[1]) * relay.exp(splitted[2])
        return relay.Function([X, W], out)

    def expected(dim):
        p0 = relay.var("p0", shape=(1, dim))
        p1 = relay.var("p1", shape=(3 * dim, dim))
        matmul = relay.nn.dense(p0, p1)
        f0 = relay.Function([p0, p1], matmul)

        p01 = relay.var("p01", shape=(1, 3 * dim))
        splitted = relay.split(p01, indices_or_sections=3, axis=1)
        out = relay.sigmoid(splitted[0]) + relay.tanh(splitted[1]) * relay.exp(splitted[2])
        f1 = relay.Function([p01], out)

        X = relay.var("X", shape=(1, dim))
        W = relay.var("W", shape=(3 * dim, dim))
        y = relay.Call(f0, [X, W])
        z = relay.Call(f1, [y])
        return relay.Function([X, W], z)

    dim = 10
    z = before(dim)
    z = relay.ir_pass.infer_type(z)
    zz = relay.ir_pass.fuse_ops(z, opt_level=0)
    assert not relay.ir_pass.free_vars(zz)
    zz = relay.ir_pass.fuse_ops(z, opt_level=2)
    zz = relay.ir_pass.infer_type(zz)
    assert not relay.ir_pass.free_vars(zz)
    after = relay.ir_pass.infer_type(expected(dim))
    assert relay.ir_pass.alpha_equal(zz, after)


def test_tuple_get_root():
    def before(dim):
        X = relay.var("X", shape=(1, 3 * dim))
        W = relay.var("W", shape=(dim, dim))
        splitted = relay.split(X, indices_or_sections=3, axis=1)
        out = relay.nn.dense(splitted[0], W)
        return relay.Function([X, W], out)

    def expected(dim):
        p0 = relay.var("p0", shape=(1, 3 * dim))
        splitted = relay.split(p0, indices_or_sections=3, axis=1)
        out = splitted[0]
        f0 = relay.Function([p0], out)

        p01 = relay.var("p01", shape=(1, dim))
        p1 = relay.var("p1", shape=(dim, dim))
        out = relay.nn.dense(p01, p1)
        f1 = relay.Function([p01, p1], out)

        X = relay.var("X", shape=(1, 3 * dim))
        W = relay.var("W", shape=(dim, dim))
        y = relay.Call(f0, [X])
        z = relay.Call(f1, [y, W])
        return relay.Function([X, W], z)

    dim = 10
    z = before(dim)
    z = relay.ir_pass.infer_type(z)
    zz = relay.ir_pass.fuse_ops(z, opt_level=0)
    assert not relay.ir_pass.free_vars(zz)
    zz = relay.ir_pass.fuse_ops(z, opt_level=2)
    zz = relay.ir_pass.infer_type(zz)
    assert not relay.ir_pass.free_vars(zz)
    after = relay.ir_pass.infer_type(expected(dim))
    assert relay.ir_pass.alpha_equal(zz, after)


fuse0 = relay.transform.FuseOps(fuse_opt_level=0)
fuse2 = relay.transform.FuseOps(fuse_opt_level=2)

def test_tuple_intermediate():
    def before(x):
        inj = relay.squeeze(x)
        y1 = relay.add(inj, relay.const(1, "float32"))
        tmp = relay.squeeze(inj)
        tmp = relay.add(tmp, relay.const(1, "float32"))
        y2 = relay.add(tmp, relay.const(1, "float32"))
        y3 = relay.add(inj, relay.const(1, "float32"))
        concat = relay.concatenate((y1, y2, y3), axis=1)
        out_inj = relay.squeeze(concat)
        out = relay.add(out_inj, relay.const(1, "float32"))
        return relay.Function(relay.ir_pass.free_vars(out), out)

    def expected(p0):
        f0 = before(p0)
        x = relay.var("x", shape=dshape)
        y = relay.Call(f0, [x])
        return relay.Function([x], y)

    dshape = (1, 16, 64, 64)
    x = relay.var("x", shape=dshape)
    orig = before(x)
    fuse0(relay.Module.from_expr(orig))
    m = fuse2(relay.Module.from_expr(orig))
    relay.build(m, 'llvm')
    after = relay.ir_pass.infer_type(expected(x))
    assert relay.ir_pass.alpha_equal(m[m.entry_func], after)


def test_tuple_consecutive():
    def gen_intermediate_tuple(x):
        y1 = relay.add(x, relay.const(1, "float32"))
        y2 = relay.add(x, relay.const(1, "float32"))
        y3 = relay.add(x, relay.const(1, "float32"))
        concat = relay.concatenate((y1, y2, y3), axis=1)
        out = relay.add(concat, relay.const(1, "float32"))
        return out

    def gen_consecutive_tuple(x):
        y1 = gen_intermediate_tuple(x)
        y2 = gen_intermediate_tuple(x)
        y3 = gen_intermediate_tuple(x)
        concat = relay.concatenate((y1, y2, y3), axis=1)
        return concat

    def before(x):
        concat = gen_consecutive_tuple(x)
        pooled = relay.nn.max_pool2d(concat, pool_size=(2, 2), strides=(2, 2), padding=(0, 0))
        out = relay.add(pooled, relay.const(1, "float32"))
        out2 = relay.add(out, relay.const(1, "float32"))
        out_tup = relay.Tuple((out, out2))
        return relay.Function(relay.ir_pass.free_vars(out_tup), out_tup)

    def expected(dshape):
        p0 = relay.var("p0", shape=dshape)
        concat = gen_consecutive_tuple(p0)
        f0 = relay.Function([p0], concat)

        p01 = relay.var("p01", shape=(1, dshape[1]*9, dshape[2], dshape[3]))
        pooled = relay.nn.max_pool2d(p01, pool_size=(2, 2), strides=(2, 2), padding=(0, 0))
        out = relay.add(pooled, relay.const(1, "float32"))
        f1 = relay.Function([p01], out)

        p02 = relay.var("p02", shape=(1, dshape[1]*9, dshape[2]//2, dshape[3]//2))
        out = relay.add(p02, relay.const(1, "float32"))
        f2 = relay.Function([p02], out)

        x = relay.var("x", shape=dshape)
        y = relay.Call(f0, [x])
        z = relay.Call(f1, [y])
        z2 = relay.Call(f2, [z])

        return relay.Function([x], relay.Tuple((z, z2)))

    dshape = (1, 16, 64, 64)
    x = relay.var("x", shape=dshape)
    orig = before(x)
    fuse0(relay.Module.from_expr(orig))
    m = fuse2(relay.Module.from_expr(orig))
    relay.build(m, 'llvm')
    after = relay.ir_pass.infer_type(expected(dshape))
    assert relay.ir_pass.alpha_equal(m[m.entry_func], after)


def test_inception_like():
    def conv(data):
        y = relay.nn.conv2d(data, relay.var("w"),
                            kernel_size=(3, 3),
                            padding=(1, 1),
                            channels=16)
        return relay.nn.relu(data=y)

    def inception_like(data):
        c0 = conv(data)
        c1 = conv(data)
        return relay.concatenate((c0, c1), axis=1)

    def before(dshape):
        x = relay.var("x", shape=dshape)
        in1 = inception_like(x)
        in2 = inception_like(in1)
        return relay.Function(relay.ir_pass.free_vars(in2), in2)

    def expected(dshape):
        p0 = relay.var("p0", shape=dshape)
        c = conv(p0)
        f0 = relay.Function(relay.ir_pass.free_vars(c), c)

        p01 = relay.var("p01", shape=dshape)
        c = conv(p01)
        f1 = relay.Function(relay.ir_pass.free_vars(c), c)

        p02 = relay.var("p02", shape=dshape)
        p12 = relay.var("p12", shape=dshape)
        concat1 = relay.concatenate((p02, p12), axis=1)
        f_concat1 = relay.Function([p02, p12], concat1)

        dshape2 = (dshape[0], dshape[1]*2, dshape[2], dshape[3])

        p03 = relay.var("p03", shape=dshape2)
        c = conv(p03)
        f2 = relay.Function(relay.ir_pass.free_vars(c), c)

        p04 = relay.var("p04", shape=dshape2)
        c = conv(p04)
        f3 = relay.Function(relay.ir_pass.free_vars(c), c)

        p05 = relay.var("p05", shape=dshape)
        p15 = relay.var("p15", shape=dshape)
        concat2 = relay.concatenate((p05, p15), axis=1)
        f_concat2 = relay.Function([p05, p15], concat2)

        x = relay.var("x", shape=dshape)
        c1 = relay.Call(f0, [x, relay.var("w1")])
        c2 = relay.Call(f1, [x, relay.var("w2")])
        concat = relay.Call(f_concat1, [c1, c2])
        c3 = relay.Call(f2, [concat, relay.var("w3")])
        c4 = relay.Call(f3, [concat, relay.var("w4")])
        out = relay.Call(f_concat2, [c3, c4])

        return relay.Function(relay.ir_pass.free_vars(out), out)

    dshape = (1, 16, 64, 64)
    orig = before(dshape)
    fuse0(relay.Module.from_expr(orig))
    m = fuse2(relay.Module.from_expr(orig))
    relay.build(m, 'llvm')
    after = relay.ir_pass.infer_type(expected(dshape))
    assert relay.ir_pass.alpha_equal(m[m.entry_func], after)


def test_fuse_parallel_injective():
    """Test fusing parallel injective ops to an elemwise op."""
    def before():
        x = relay.var("x", shape=(10, 20))
        y = relay.add(x, relay.const(1, "float32"))
        z = relay.squeeze(y)
        u = relay.transpose(y, axes=[0, 1])
        w = relay.left_shift(z, u)
        return relay.Function([x], w)

    def expected():
        x = relay.var("p", shape=(10, 20))
        y = relay.add(x, relay.const(1, "float32"))
        z = relay.squeeze(y)
        u = relay.transpose(y, axes=[0, 1])
        w = relay.left_shift(z, u)
        f1 = relay.Function([x], w)
        x = relay.var("x", shape=(10, 20))
        y = relay.Call(f1, [x])
        return relay.Function([x], y)

    z = before()
    z = relay.ir_pass.infer_type(z)
    zz = relay.ir_pass.fuse_ops(z, opt_level=0)
    assert not relay.ir_pass.free_vars(zz)
    zz = relay.ir_pass.fuse_ops(z, opt_level=2)
    zz = relay.ir_pass.infer_type(zz)
    assert not relay.ir_pass.free_vars(zz)
    after = relay.ir_pass.infer_type(expected())
    assert relay.ir_pass.alpha_equal(zz, after)


if __name__ == "__main__":
    test_fuse_simple()
    test_conv2d_fuse()
    test_concatenate()
    test_tuple_root()
    test_stop_fusion()
    test_fuse_myia_regression()
    test_fuse_tuple_get_elemwise()
    test_tuple_get_root()
    test_tuple_intermediate()
    test_tuple_consecutive()
    test_inception_like()
    test_fuse_parallel_injective()

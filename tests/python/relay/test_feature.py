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
from tvm.relay.ir_pass import detect_feature, gradient
from tvm.relay.feature import Feature
from tvm.relay.prelude import Prelude

def test_prelude():
    p = Prelude()
    feats = detect_feature(p.mod)
    assert feats == set([
        Feature.fVar,
        Feature.fGlobalVar,
        Feature.fConstant,
        Feature.fTuple,
        Feature.fTupleGetItem,
        Feature.fFunction,
        Feature.fOp,
        Feature.fCall,
        Feature.fLet,
        Feature.fIf,
        Feature.fConstructor,
        Feature.fMatch
    ])


def test_ad():
    shape = (10, 10)
    dtype = 'float32'
    t = relay.TensorType(shape, dtype)
    x = relay.var("x", t)
    func = relay.Function([x], x + x)
    back_func = relay.ir_pass.infer_type(gradient(func))
    feats = detect_feature(back_func)
    assert feats == set([
        Feature.fVar,
        Feature.fTuple,
        Feature.fTupleGetItem,
        Feature.fFunction,
        Feature.fOp,
        Feature.fCall,
        Feature.fLet,
        Feature.fRefCreate,
        Feature.fRefRead,
        Feature.fRefWrite
    ])


if __name__ == '__main__':
    test_prelude()
    test_ad()

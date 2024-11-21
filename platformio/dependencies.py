# Copyright (c) 2014-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import platform

from platformio.compat import PY36, is_proxy_set


def get_core_dependencies():
    return {
        "contrib-piohome": "~3.4.2",
        "contrib-pioremote": "~1.0.0",
        "tool-scons": "~4.40801.0",
        "tool-cppcheck": "~1.21100.0",
        "tool-clangtidy": "~1.150005.0",
        "tool-pvs-studio": "~7.18.0",
    }


def get_pip_dependencies():
    core = [
    ]

    home = [
        # PIO Home requirements
        "ajsonrpc == 1.2.*",
        "starlette >=0.19, <0.39",
        "uvicorn %s" % ("== 0.16.0" if PY36 else ">=0.16, <0.31"),
        "wsproto == 1.*",
    ]

    extra = []

    # issue #4702; Broken "requests/charset_normalizer" on macOS ARM
    if platform.system() == "Darwin" and "arm" in platform.machine().lower():
        extra.append("chardet>=3.0.2,<6")

    # issue 4614: urllib3 v2.0 only supports OpenSSL 1.1.1+
    try:
        import ssl  # pylint: disable=import-outside-toplevel

        if ssl.OPENSSL_VERSION.startswith("OpenSSL ") and ssl.OPENSSL_VERSION_INFO < (
            1,
            1,
            1,
        ):
            extra.append("urllib3<2")
    except ImportError:
        pass

    return core + home + extra
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

import os
import shutil
import time
from pathlib import Path

import semantic_version
from ajsonrpc.core import JSONRPC20DispatchException

from platformio import app, exception, fs
from platformio.home.rpc.handlers.app import AppRPC
from platformio.home.rpc.handlers.base import BaseRPCHandler
from platformio.home.rpc.handlers.piocore import PIOCoreRPC
from platformio.package.manager.platform import PlatformPackageManager
from platformio.platform.factory import PlatformFactory
from platformio.project.config import ProjectConfig
from platformio.project.exception import ProjectError
from platformio.project.helpers import get_project_dir, is_platformio_project
from platformio.project.integration.generator import ProjectGenerator
from platformio.project.options import get_config_options_schema


class ProjectRPC(BaseRPCHandler):
    @staticmethod
    def config_call(init_kwargs, method, *args):
        assert isinstance(init_kwargs, dict)
        assert "path" in init_kwargs
        if os.path.isdir(init_kwargs["path"]):
            project_dir = init_kwargs["path"]
            init_kwargs["path"] = os.path.join(init_kwargs["path"], "platformio.ini")
        elif os.path.isfile(init_kwargs["path"]):
            project_dir = os.path.dirname(init_kwargs["path"])
        else:
            project_dir = get_project_dir()
        with fs.cd(project_dir):
            return getattr(ProjectConfig(**init_kwargs), method)(*args)

    @staticmethod
    def config_load(path):
        return ProjectConfig(
            path, parse_extra=False, expand_interpolations=False
        ).as_tuple()

    @staticmethod
    def config_dump(path, data):
        config = ProjectConfig(path, parse_extra=False, expand_interpolations=False)
        config.update(data, clear=True)
        return config.save()

    @staticmethod
    def config_update_description(path, text):
        config = ProjectConfig(path, parse_extra=False, expand_interpolations=False)
        if not config.has_section("platformio"):
            config.add_section("platformio")
        if text:
            config.set("platformio", "description", text)
        else:
            if config.has_option("platformio", "description"):
                config.remove_option("platformio", "description")
            if not config.options("platformio"):
                config.remove_section("platformio")
        return config.save()

    @staticmethod
    def get_config_schema():
        return get_config_options_schema()

    @staticmethod
    def get_projects():
        def _get_project_data():
            data = {"boards": [], "envLibdepsDirs": [], "libExtraDirs": []}
            config = ProjectConfig()
            data["envs"] = config.envs()
            data["description"] = config.get("platformio", "description")
            data["libExtraDirs"].extend(config.get("platformio", "lib_extra_dirs", []))

            libdeps_dir = config.get("platformio", "libdeps_dir")
            for section in config.sections():
                if not section.startswith("env:"):
                    continue
                data["envLibdepsDirs"].append(os.path.join(libdeps_dir, section[4:]))
                if config.has_option(section, "board"):
                    data["boards"].append(config.get(section, "board"))
                data["libExtraDirs"].extend(config.get(section, "lib_extra_dirs", []))

            # skip non existing folders and resolve full path
            for key in ("envLibdepsDirs", "libExtraDirs"):
                data[key] = [
                    fs.expanduser(d) if d.startswith("~") else os.path.abspath(d)
                    for d in data[key]
                    if os.path.isdir(d)
                ]

            return data

        def _path_to_name(path):
            return (os.path.sep).join(path.split(os.path.sep)[-2:])

        result = []
        pm = PlatformPackageManager()
        for project_dir in AppRPC.load_state()["storage"]["recentProjects"]:
            if not os.path.isdir(project_dir):
                continue
            data = {}
            boards = []
            try:
                with fs.cd(project_dir):
                    data = _get_project_data()
            except ProjectError:
                continue

            for board_id in data.get("boards", []):
                name = board_id
                try:
                    name = pm.board_config(board_id)["name"]
                except exception.PlatformioException:
                    pass
                boards.append({"id": board_id, "name": name})

            result.append(
                {
                    "path": project_dir,
                    "name": _path_to_name(project_dir),
                    "modified": int(os.path.getmtime(project_dir)),
                    "boards": boards,
                    "description": data.get("description"),
                    "envs": data.get("envs", []),
                    "envLibStorages": [
                        {"name": os.path.basename(d), "path": d}
                        for d in data.get("envLibdepsDirs", [])
                    ],
                    "extraLibStorages": [
                        {"name": _path_to_name(d), "path": d}
                        for d in data.get("libExtraDirs", [])
                    ],
                }
            )
        return result

    @staticmethod
    def get_project_examples():
        result = []
        pm = PlatformPackageManager()
        for pkg in pm.get_installed():
            examples_dir = os.path.join(pkg.path, "examples")
            if not os.path.isdir(examples_dir):
                continue
            items = []
            for project_dir, _, __ in os.walk(examples_dir):
                project_description = None
                try:
                    config = ProjectConfig(os.path.join(project_dir, "platformio.ini"))
                    config.validate(silent=True)
                    project_description = config.get("platformio", "description")
                except ProjectError:
                    continue

                path_tokens = project_dir.split(os.path.sep)
                items.append(
                    {
                        "name": "/".join(
                            path_tokens[path_tokens.index("examples") + 1 :]
                        ),
                        "path": project_dir,
                        "description": project_description,
                    }
                )
            manifest = pm.load_manifest(pkg)
            result.append(
                {
                    "platform": {
                        "title": manifest["title"],
                        "version": manifest["version"],
                    },
                    "items": sorted(items, key=lambda item: item["name"]),
                }
            )
        return sorted(result, key=lambda data: data["platform"]["title"])

    async def init(self, board, framework, project_dir):
        assert project_dir
        if not os.path.isdir(project_dir):
            os.makedirs(project_dir)
        args = ["init", "--board", board, "--sample-code"]
        if framework:
            args.extend(["--project-option", "framework = %s" % framework])
        ide = app.get_session_var("caller_id")
        if ide in ProjectGenerator.get_supported_ides():
            args.extend(["--ide", ide])
        await PIOCoreRPC.call(
            args, options={"cwd": project_dir, "force_subprocess": True}
        )
        return project_dir

    @staticmethod
    async def import_arduino(board, use_arduino_libs, arduino_project_dir):
        board = str(board)
        # don't import PIO Project
        if is_platformio_project(arduino_project_dir):
            return arduino_project_dir

        is_arduino_project = any(
            os.path.isfile(
                os.path.join(
                    arduino_project_dir,
                    "%s.%s" % (os.path.basename(arduino_project_dir), ext),
                )
            )
            for ext in ("ino", "pde")
        )
        if not is_arduino_project:
            raise JSONRPC20DispatchException(
                code=4000, message="Not an Arduino project: %s" % arduino_project_dir
            )

        state = AppRPC.load_state()
        project_dir = os.path.join(
            state["storage"]["projectsDir"], time.strftime("%y%m%d-%H%M%S-") + board
        )
        if not os.path.isdir(project_dir):
            os.makedirs(project_dir)
        args = ["init", "--board", board]
        args.extend(["--project-option", "framework = arduino"])
        if use_arduino_libs:
            args.extend(
                ["--project-option", "lib_extra_dirs = ~/Documents/Arduino/libraries"]
            )
        ide = app.get_session_var("caller_id")
        if ide in ProjectGenerator.get_supported_ides():
            args.extend(["--ide", ide])
        await PIOCoreRPC.call(
            args, options={"cwd": project_dir, "force_subprocess": True}
        )
        with fs.cd(project_dir):
            config = ProjectConfig()
            src_dir = config.get("platformio", "src_dir")
            if os.path.isdir(src_dir):
                fs.rmtree(src_dir)
            shutil.copytree(arduino_project_dir, src_dir, symlinks=True)
        return project_dir

    @staticmethod
    async def import_pio(project_dir):
        if not project_dir or not is_platformio_project(project_dir):
            raise JSONRPC20DispatchException(
                code=4001, message="Not an PlatformIO project: %s" % project_dir
            )
        new_project_dir = os.path.join(
            AppRPC.load_state()["storage"]["projectsDir"],
            time.strftime("%y%m%d-%H%M%S-") + os.path.basename(project_dir),
        )
        shutil.copytree(project_dir, new_project_dir, symlinks=True)

        args = ["init"]
        ide = app.get_session_var("caller_id")
        if ide in ProjectGenerator.get_supported_ides():
            args.extend(["--ide", ide])
        await PIOCoreRPC.call(
            args, options={"cwd": new_project_dir, "force_subprocess": True}
        )
        return new_project_dir

    async def init_v2(self, configuration, options=None):
        project_dir = os.path.join(configuration["location"], configuration["name"])
        if not os.path.isdir(project_dir):
            os.makedirs(project_dir)

        envclone = os.environ.copy()
        envclone["PLATFORMIO_FORCE_ANSI"] = "true"
        options = options or {}
        options["spawn"] = {"env": envclone, "cwd": project_dir}

        args = ["project", "init"]
        ide = app.get_session_var("caller_id")
        if ide in ProjectGenerator.get_supported_ides():
            args.extend(["--ide", ide])

        if configuration.get("example"):
            await self.factory.notify_clients(
                method=options.get("stdoutNotificationMethod"),
                params=["Copying example files...\n"],
                actor="frontend",
            )
            await self._pre_init_example(configuration, project_dir)
        else:
            args.extend(self._pre_init_empty(configuration))

        return await self.factory.manager.dispatcher["core.exec"](args, options=options)

    @staticmethod
    def _pre_init_empty(configuration):
        project_options = []
        platform = configuration["platform"]
        board_id = configuration.get("board", {}).get("id")
        env_name = board_id or platform["name"]
        if configuration.get("description"):
            project_options.append(("description", configuration.get("description")))
        try:
            v = semantic_version.Version(platform.get("version"))
            assert not v.prerelease
            project_options.append(
                ("platform", "{name} @ ^{version}".format(**platform))
            )
        except (AssertionError, ValueError):
            project_options.append(
                ("platform", "{name} @ {version}".format(**platform))
            )
        if board_id:
            project_options.append(("board", board_id))
        if configuration.get("framework"):
            project_options.append(("framework", configuration["framework"]["name"]))

        args = ["-e", env_name, "--sample-code"]
        for name, value in project_options:
            args.extend(["-O", f"{name}={value}"])
        return args

    async def _pre_init_example(self, configuration, project_dir):
        for item in configuration["example"]["files"]:
            p = Path(project_dir).joinpath(item["path"])
            if not p.parent.is_dir():
                p.parent.mkdir(parents=True)
            p.write_text(
                await self.factory.manager.dispatcher["os.request_content"](
                    item["url"]
                ),
                encoding="utf-8",
            )
        return []

    @staticmethod
    def configuration(project_dir, env):
        assert is_platformio_project(project_dir)
        with fs.cd(project_dir):
            config = ProjectConfig(os.path.join(project_dir, "platformio.ini"))
            platform = PlatformFactory.from_env(env, autoinstall=True)
            platform_pkg = PlatformPackageManager().get_package(platform.get_dir())
            board_id = config.get(f"env:{env}", "board", None)

            # frameworks
            frameworks = []
            for name in config.get(f"env:{env}", "framework", []):
                if name not in platform.frameworks:
                    continue
                f_pkg_name = platform.frameworks[name].get("package")
                if not f_pkg_name:
                    continue
                f_pkg = platform.get_package(f_pkg_name)
                if not f_pkg:
                    continue
                f_manifest = platform.pm.load_manifest(f_pkg)
                frameworks.append(
                    dict(
                        name=name,
                        title=f_manifest.get("title"),
                        version=str(f_pkg.metadata.version),
                    )
                )

            return dict(
                platform=dict(
                    ownername=(
                        platform_pkg.metadata.spec.owner
                        if platform_pkg.metadata.spec
                        else None
                    ),
                    name=platform.name,
                    title=platform.title,
                    version=str(platform_pkg.metadata.version),
                ),
                board=(
                    platform.board_config(board_id).get_brief_data()
                    if board_id
                    else None
                ),
                frameworks=frameworks or None,
            )

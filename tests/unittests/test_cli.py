# This file is part of cloud-init. See LICENSE file for license information.

import contextlib
import io
import os
from collections import namedtuple

import pytest

from cloudinit import helpers
from cloudinit.cmd import main as cli
from cloudinit.util import load_file, load_json
from tests.unittests import helpers as test_helpers

mock = test_helpers.mock

M_PATH = "cloudinit.cmd.main."


@pytest.fixture(autouse=False)
def mock_get_user_data_file(mocker, tmpdir):
    yield mocker.patch(
        "cloudinit.cmd.devel.logs._get_user_data_file",
        return_value=tmpdir.join("cloud"),
    )


class TestCLI:
    def _call_main(self, sysv_args=None):
        if not sysv_args:
            sysv_args = ["cloud-init"]
        try:
            return cli.main(sysv_args=sysv_args)
        except SystemExit as e:
            return e.code

    @pytest.mark.parametrize(
        "action,name,match",
        [
            pytest.param(
                "doesnotmatter",
                "init1",
                "^unknown name: init1$",
                id="invalid_name",
            ),
            pytest.param(
                "modules_name",
                "modules",
                "^Invalid cloud init mode specified 'modules-bogusmode'$",
                id="invalid_modes",
            ),
        ],
    )
    def test_status_wrapper_errors(self, action, name, match, caplog, tmpdir):
        data_d = tmpdir.join("data")
        link_d = tmpdir.join("link")
        FakeArgs = namedtuple("FakeArgs", ["action", "local", "mode"])

        def myaction():
            raise Exception("Should not call myaction")

        myargs = FakeArgs((action, myaction), False, "bogusmode")
        with pytest.raises(ValueError, match=match):
            cli.status_wrapper(name, myargs, data_d, link_d)
        assert "Should not call myaction" not in caplog.text

    def test_status_wrapper_init_local_writes_fresh_status_info(self, tmpdir):
        """When running in init-local mode, status_wrapper writes status.json.

        Old status and results artifacts are also removed.
        """
        data_d = tmpdir.join("data")
        link_d = tmpdir.join("link")
        status_link = link_d.join("status.json")
        # Write old artifacts which will be removed or updated.
        for _dir in data_d, link_d:
            test_helpers.populate_dir(
                str(_dir), {"status.json": "old", "result.json": "old"}
            )

        FakeArgs = namedtuple("FakeArgs", ["action", "local", "mode"])

        def myaction(name, args):
            # Return an error to watch status capture them
            return "SomeDatasource", ["an error"]

        myargs = FakeArgs(("ignored_name", myaction), True, "bogusmode")
        cli.status_wrapper("init", myargs, data_d, link_d)
        # No errors reported in status
        status_v1 = load_json(load_file(status_link))["v1"]
        assert ["an error"] == status_v1["init-local"]["errors"]
        assert "SomeDatasource" == status_v1["datasource"]
        assert False is os.path.exists(
            data_d.join("result.json")
        ), "unexpected result.json found"
        assert False is os.path.exists(
            link_d.join("result.json")
        ), "unexpected result.json link found"

    def test_status_wrapper_init_local_honor_cloud_dir(self, mocker, tmpdir):
        """When running in init-local mode, status_wrapper honors cloud_dir."""
        cloud_dir = tmpdir.join("cloud")
        paths = helpers.Paths({"cloud_dir": str(cloud_dir)})
        mocker.patch(M_PATH + "read_cfg_paths", return_value=paths)
        data_d = cloud_dir.join("data")
        link_d = tmpdir.join("link")

        FakeArgs = namedtuple("FakeArgs", ["action", "local", "mode"])

        def myaction(name, args):
            # Return an error to watch status capture them
            return "SomeDatasource", ["an_error"]

        myargs = FakeArgs(("ignored_name", myaction), True, "bogusmode")
        cli.status_wrapper("init", myargs, link_d=link_d)  # No explicit data_d
        # Access cloud_dir directly
        status_v1 = load_json(load_file(data_d.join("status.json")))["v1"]
        assert ["an_error"] == status_v1["init-local"]["errors"]
        assert "SomeDatasource" == status_v1["datasource"]
        assert False is os.path.exists(
            data_d.join("result.json")
        ), "unexpected result.json found"
        assert False is os.path.exists(
            link_d.join("result.json")
        ), "unexpected result.json link found"

    def test_no_arguments_shows_usage(self, capsys):
        exit_code = self._call_main()
        _out, err = capsys.readouterr()
        assert "usage: cloud-init" in err
        assert 2 == exit_code

    def test_no_arguments_shows_error_message(self, capsys):
        exit_code = self._call_main()
        missing_subcommand_message = (
            "the following arguments are required: subcommand"
        )
        _out, err = capsys.readouterr()
        assert (
            missing_subcommand_message in err
        ), "Did not find error message for missing subcommand"
        assert 2 == exit_code

    def test_all_subcommands_represented_in_help(self, capsys):
        """All known subparsers are represented in the cloud-int help doc."""
        self._call_main()
        _out, err = capsys.readouterr()
        expected_subcommands = [
            "analyze",
            "clean",
            "devel",
            "dhclient-hook",
            "features",
            "init",
            "modules",
            "single",
            "schema",
        ]
        for subcommand in expected_subcommands:
            assert subcommand in err

    @pytest.mark.parametrize("subcommand", ["init", "modules"])
    @mock.patch("cloudinit.cmd.main.status_wrapper")
    def test_modules_subcommand_parser(self, m_status_wrapper, subcommand):
        """The subcommand 'subcommand' calls status_wrapper passing modules."""
        self._call_main(["cloud-init", subcommand])
        (name, parseargs) = m_status_wrapper.call_args_list[0][0]
        assert subcommand == name
        assert subcommand == parseargs.subcommand
        assert subcommand == parseargs.action[0]
        assert f"main_{subcommand}" == parseargs.action[1].__name__

    @pytest.mark.parametrize(
        "subcommand",
        [
            "analyze",
            "clean",
            "collect-logs",
            "devel",
            "status",
            "schema",
        ],
    )
    def test_conditional_subcommands_from_entry_point_sys_argv(
        self, subcommand, capsys, mock_get_user_data_file, tmpdir
    ):
        """Subcommands from entry-point are properly parsed from sys.argv."""
        expected_error = f"usage: cloud-init {subcommand}"
        # The cloud-init entrypoint calls main without passing sys_argv
        with mock.patch("sys.argv", ["cloud-init", subcommand, "-h"]):
            try:
                cli.main()
            except SystemExit as e:
                assert 0 == e.code  # exit 2 on proper -h usage
        out, _err = capsys.readouterr()
        assert expected_error in out

    @pytest.mark.parametrize(
        "subcommand",
        [
            "clean",
            "collect-logs",
            "status",
        ],
    )
    def test_subcommand_parser(self, subcommand, mock_get_user_data_file):
        """cloud-init `subcommand` calls its subparser."""
        # Provide -h param to `subcommand` to avoid having to mock behavior.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self._call_main(["cloud-init", subcommand, "-h"])
        assert f"usage: cloud-init {subcommand}" in out.getvalue()

    @pytest.mark.parametrize(
        "args,expected_subcommands",
        [
            ([], ["schema"]),
            (["analyze"], ["blame", "show", "dump"]),
        ],
    )
    def test_subcommand_parser_multi_arg(
        self, args, expected_subcommands, capsys
    ):
        """The subcommand cloud-init schema calls the correct subparser."""
        self._call_main(["cloud-init"] + args)
        _out, err = capsys.readouterr()
        for subcommand in expected_subcommands:
            assert subcommand in err

    def test_wb_schema_subcommand_parser(self, capsys):
        """The subcommand cloud-init schema calls the correct subparser."""
        exit_code = self._call_main(["cloud-init", "schema"])
        _out, err = capsys.readouterr()
        assert 1 == exit_code
        # Known whitebox output from schema subcommand
        assert (
            "Error:\n"
            "Expected one of --config-file, --system or --docs arguments\n"
            == err
        )

    @pytest.mark.parametrize(
        "args,expected_doc_sections,is_error",
        [
            pytest.param(
                ["all"],
                [
                    "**Supported distros:** all",
                    "**Supported distros:** almalinux, alpine, centos, "
                    "cloudlinux, cos, debian, eurolinux, fedora, freebsd, "
                    "mariner, miraclelinux, "
                    "openbsd, openEuler, OpenCloudOS, openmandriva, "
                    "opensuse, opensuse-microos, opensuse-tumbleweed, "
                    "opensuse-leap, photon, rhel, rocky, sle_hpc, "
                    "sle-micro, sles, TencentOS, ubuntu, virtuozzo",
                    "**Config schema**:\n    **resize_rootfs:** "
                    "(``true``/``false``/``noblock``)",
                    "**Examples**::\n\n    runcmd:\n        - [ ls, -l, / ]\n",
                ],
                False,
                id="all_spot_check",
            ),
            pytest.param(
                ["cc_runcmd"],
                ["Runcmd\n------\n**Summary:** Run arbitrary commands"],
                False,
                id="single_spot_check",
            ),
            pytest.param(
                [
                    "cc_runcmd",
                    "cc_resizefs",
                ],
                [
                    "Runcmd\n------\n**Summary:** Run arbitrary commands",
                    "Resizefs\n--------\n**Summary:** Resize filesystem",
                ],
                False,
                id="multiple_spot_check",
            ),
            pytest.param(
                ["garbage_value"],
                ["Invalid --docs value"],
                True,
                id="bad_arg_fails",
            ),
        ],
    )
    def test_wb_schema_subcommand(self, args, expected_doc_sections, is_error):
        """Validate that doc content has correct values."""

        # Note: patchStdoutAndStderr() is convenient for reducing boilerplate,
        # but inspecting the code for debugging is not ideal
        # contextlib.redirect_stdout() provides similar behavior as a context
        # manager
        out_or_err = io.StringIO()
        redirecter = (
            contextlib.redirect_stderr
            if is_error
            else contextlib.redirect_stdout
        )
        with redirecter(out_or_err):
            self._call_main(["cloud-init", "schema", "--docs"] + args)
        out_or_err = out_or_err.getvalue()
        for expected in expected_doc_sections:
            assert expected in out_or_err

    @mock.patch("cloudinit.cmd.main.main_single")
    def test_single_subcommand(self, m_main_single):
        """The subcommand 'single' calls main_single with valid args."""
        self._call_main(["cloud-init", "single", "--name", "cc_ntp"])
        (name, parseargs) = m_main_single.call_args_list[0][0]
        assert "single" == name
        assert "single" == parseargs.subcommand
        assert "single" == parseargs.action[0]
        assert False is parseargs.debug
        assert False is parseargs.force
        assert None is parseargs.frequency
        assert "cc_ntp" == parseargs.name
        assert False is parseargs.report

    @mock.patch("cloudinit.cmd.main.dhclient_hook.handle_args")
    def test_dhclient_hook_subcommand(self, m_handle_args):
        """The subcommand 'dhclient-hook' calls dhclient_hook with args."""
        self._call_main(["cloud-init", "dhclient-hook", "up", "eth0"])
        (name, parseargs) = m_handle_args.call_args_list[0][0]
        assert "dhclient-hook" == name
        assert "dhclient-hook" == parseargs.subcommand
        assert "dhclient-hook" == parseargs.action[0]
        assert False is parseargs.debug
        assert False is parseargs.force
        assert "up" == parseargs.event
        assert "eth0" == parseargs.interface

    @mock.patch("cloudinit.cmd.main.main_features")
    def test_features_hook_subcommand(self, m_features):
        """The subcommand 'features' calls main_features with args."""
        self._call_main(["cloud-init", "features"])
        (name, parseargs) = m_features.call_args_list[0][0]
        assert "features" == name
        assert "features" == parseargs.subcommand
        assert "features" == parseargs.action[0]
        assert False is parseargs.debug
        assert False is parseargs.force


# : ts=4 expandtab

"""No provider calls: native npm talks only to an owned loopback registry."""

import base64
from contextlib import redirect_stdout
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from package_inventory import (catalogue, collect, InventoryGap, locked_versions, main,
                               LOCAL_VENDOR_LINKS, NpmReader, REGISTRY, SCOPES, sri, verify_archive)


NAME = "@merglbot-proteinaco/btf-viz"
VERSIONS = ("0.5.2", "0.5.3", "0.6.0-beta.1")


def integrity(data):
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")


def archive(version):
    metadata = json.dumps({"name": NAME, "version": version, "scripts": {
        "prepack": "node -e 'process.exit(77)'", "prepare": "node -e 'process.exit(78)'"
    }}).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        entry = tarfile.TarInfo("package/package.json")
        entry.size = len(metadata)
        tar.addfile(entry, io.BytesIO(metadata))
    return output.getvalue()


class FakeReader:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.bytes = {v: archive(v) for v in VERSIONS}
        self.rows = {v: {"version": v, "dist.integrity": integrity(self.bytes[v]),
                         "dist.tarball": REGISTRY + "/download/" + v,
                         "dist.unpackedSize": 4096, "dist.fileCount": 1}
                     for v in VERSIONS}
        self.tags = {"latest": "0.5.3", "next": "0.6.0-beta.1"}

    def __call__(self, args, cwd):
        self.calls.append(args)
        if args[0] == "view":
            if args[1] == NAME:
                return {"versions": list(self.rows), "dist-tags": copy.deepcopy(self.tags)}
            if args[1] == NAME + "@*":
                return [copy.deepcopy(self.rows[v]) for v in VERSIONS[:2]]
            return copy.deepcopy(self.rows[args[1].split("@")[-1]])
        version = args[1].split("@")[-1]
        target = Path(cwd) / "package.tgz"
        target.write_bytes(self.bytes[version])
        return [{"name": NAME, "version": version, "filename": target.name,
                 "size": target.stat().st_size, "integrity": integrity(self.bytes[version])}]


class LocalVendorLinkTests(unittest.TestCase):
    def setUp(self):
        self.allowed = SCOPES["core"][1]
        self.links = LOCAL_VENDOR_LINKS["core"]
        self.lock = {"lockfileVersion": 3, "packages": {"": {"dependencies": {}}}}
        packages = self.lock["packages"]
        for name in self.allowed:
            packages["node_modules/" + name] = {
                "version": "1.0.0", "resolved": REGISTRY + "/download/fixture",
                "integrity": integrity(b"fixture")}
        for name, target in self.links.items():
            packages[""]["dependencies"][name] = "file:" + target
            packages["node_modules/" + name] = {"resolved": target, "link": True}
            packages[target] = {"name": name, "version": "0.1.0"}

    def pins(self):
        return locked_versions(self.lock, self.allowed, local_links=self.links)

    def test_current_admin_link_shape_preserves_both_registry_pins(self):
        pins = self.pins()
        self.assertEqual(set(pins), set(self.allowed))
        self.assertTrue(all(value == {"1.0.0": integrity(b"fixture")} for value in pins.values()))
        with self.assertRaisesRegex(InventoryGap, "^unmapped_private_package$"):
            locked_versions(self.lock, self.allowed)

    def test_changed_link_target_identity_or_declaration_fails_closed(self):
        original = copy.deepcopy(self.lock)
        name, target = next(iter(self.links.items()))
        changes = (
            lambda p: p["node_modules/" + name].update(link=False),
            lambda p: p["node_modules/" + name].update(resolved="../outside"),
            lambda p: p["node_modules/" + name].update(name="@other/alias"),
            lambda p: p.pop(target),
            lambda p: p[target].update(name="@other/alias"),
            lambda p: p[target].update(version=True),
            lambda p: p[target].update(resolved=REGISTRY + "/download/fixture"),
            lambda p: p[target].update(integrity=integrity(b"fixture")),
            lambda p: p[target].update(link=True),
            lambda p: p[""]["dependencies"].pop(name),
            lambda p: p[""]["dependencies"].update({name: "file:../outside"}),
            lambda p: p[""].update(devDependencies={name: "^0.1.0"}),
        )
        for index, change in enumerate(changes):
            self.lock = copy.deepcopy(original)
            change(self.lock["packages"])
            with self.subTest(change=index), self.assertRaisesRegex(
                    InventoryGap, "^invalid_local_vendor_link$"):
                self.pins()

    def test_unknown_or_nested_private_link_is_never_silently_omitted(self):
        name, target = next(iter(self.links.items()))
        for path, reason in (("node_modules/@merglbot-core/unknown", "unmapped_private_package"),
                             ("node_modules/parent/node_modules/" + name, "invalid_local_vendor_link")):
            with self.subTest(path=path):
                self.lock["packages"][path] = {"resolved": target, "link": True}
                with self.assertRaisesRegex(InventoryGap, "^" + reason + "$"):
                    self.pins()
                del self.lock["packages"][path]

    def test_known_registry_packages_cannot_be_reclassified_as_local(self):
        name = self.allowed[0]
        with self.assertRaisesRegex(InventoryGap, "^invalid_local_vendor_bindings$"):
            locked_versions(self.lock, self.allowed, local_links={name: "vendor/fixture/package"})

    def test_missing_registry_package_still_blocks_with_valid_vendor_links(self):
        del self.lock["packages"]["node_modules/" + self.allowed[0]]
        with self.assertRaisesRegex(InventoryGap, "^required_package_missing$"):
            self.pins()

    def test_declared_vendor_binding_requires_its_link_row(self):
        original = copy.deepcopy(self.lock)
        for version in (2, 3):
            for name, target in self.links.items():
                for field in ("dependencies", "devDependencies", "optionalDependencies"):
                    for keep_vendor_metadata in (False, True):
                        with self.subTest(version=version, name=name, field=field,
                                          keep_vendor_metadata=keep_vendor_metadata):
                            self.lock = copy.deepcopy(original)
                            self.lock["lockfileVersion"] = version
                            packages = self.lock["packages"]
                            del packages["node_modules/" + name]
                            del packages[""]["dependencies"][name]
                            packages[""].setdefault(field, {})[name] = "file:" + target
                            if not keep_vendor_metadata:
                                del packages[target]
                            with self.assertRaisesRegex(
                                    InventoryGap, "^invalid_local_vendor_link$"):
                                self.pins()

    def test_missing_declared_link_blocks_collection_before_reader_creation(self):
        name = next(iter(self.links))
        del self.lock["packages"]["node_modules/" + name]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package-lock.json"
            path.write_text(json.dumps(self.lock))
            reader_factory = Mock(side_effect=AssertionError("unexpected_reader_creation"))
            with self.assertRaisesRegex(InventoryGap, "^invalid_local_vendor_link$"):
                collect("core", path, env={"GITHUB_ACTIONS": "true",
                        "GITHUB_REPOSITORY": SCOPES["core"][0]},
                        reader_factory=reader_factory)
            reader_factory.assert_not_called()

    def test_report_separates_vendor_metadata_from_archive_and_identity_proof(self):
        metadata = {"versions": {"1.0.0": {"integrity": integrity(b"fixture")}}, "dist_tags": {}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package-lock.json"
            path.write_text(json.dumps(self.lock))
            with patch("package_inventory.catalogue", side_effect=lambda *args: copy.deepcopy(metadata)) as cat, \
                 patch("package_inventory.verify_archive", return_value={"compressed_bytes": 7}), \
                 patch("package_inventory.NpmReader") as reader:
                result = collect("core", path, env={"GITHUB_ACTIONS": "true",
                                 "GITHUB_REPOSITORY": SCOPES["core"][0]})
        self.assertEqual({c.args[1] for c in cat.call_args_list}, set(self.allowed))
        reader.return_value.assert_not_called()
        self.assertEqual({x["name"] for x in result["local_vendor_links"]}, set(self.links))
        self.assertTrue(all(not x["filesystem_content_verified"]
                            and not x["registry_archive_bytes_measured"]
                            for x in result["local_vendor_links"]))
        self.assertFalse(result["effective_auth_identity_verified"])
        self.assertFalse(result["all_catalogue_archive_bytes_complete"])


class PackageInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reader = FakeReader(self.root)
        self.lock = {"lockfileVersion": 3, "packages": {
            "": {}, "node_modules/parent/node_modules/" + NAME: {
                "version": "0.5.3", "resolved": REGISTRY + "/download/0.5.3",
                "integrity": self.reader.rows["0.5.3"]["dist.integrity"]}}}
        self.lock_path = self.root / "package-lock.json"
        self.lock_path.write_text(json.dumps(self.lock))
        self.env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": SCOPES["proteinaco"][0]}

    def run_collect(self, reader=None):
        return collect("proteinaco", self.lock_path, env=self.env,
                       reader_factory=lambda root, **kwargs: reader or self.reader)

    def test_nested_lock_and_prereleases_preserve_all_versions_and_tags(self):
        report = self.run_collect()
        item = report["packages"][NAME]
        self.assertEqual(set(item["versions"]), set(VERSIONS))
        self.assertEqual(item["dist_tags"], self.reader.tags)
        measured = item["locked_archives"]["0.5.3"]
        self.assertEqual(measured["compressed_bytes"], len(self.reader.bytes["0.5.3"]))
        self.assertEqual(measured["integrity"], self.lock["packages"][next(
            p for p in self.lock["packages"] if p)]["integrity"])
        self.assertFalse(report["all_catalogue_archive_bytes_complete"])
        self.assertFalse(report["effective_auth_identity_verified"])
        self.assertTrue(report["catalogue_complete"])
        self.assertFalse(list(self.root.glob("*.tgz")))
        serialized = json.dumps(report)
        self.assertNotIn("tarball", serialized)
        self.assertNotIn("download/", serialized)

    def test_context_rejected_before_lock_read_or_npm(self):
        for key, value in (("GITHUB_ACTIONS", "false"), ("GITHUB_REPOSITORY", "other/repo")):
            with self.subTest(key=key), patch.dict(self.env, {key: value}):
                with self.assertRaisesRegex(InventoryGap, "^untrusted_caller_context$"):
                    self.run_collect()
        self.assertEqual(self.reader.calls, [])

    def test_unknown_private_dependency_is_not_silently_omitted(self):
        self.lock["packages"]["node_modules/@other/unknown"] = {
            "version": "1.0.0", "resolved": REGISTRY + "/download/unknown"}
        with self.assertRaisesRegex(InventoryGap, "^unmapped_private_package$"):
            locked_versions(self.lock, (NAME,))

    def test_registry_classification_uses_actual_normalized_hostname(self):
        for url in ("https://registry.npmjs.org/npm.pkg.github.com/archive.tgz",
                    "https://npm.pkg.github.com.example.invalid/archive.tgz"):
            with self.subTest(public=url):
                lock = copy.deepcopy(self.lock)
                lock["packages"]["node_modules/public-package"] = {"resolved": url}
                self.assertEqual(set(locked_versions(lock, (NAME,))), {NAME})
        for url in ("https://NPM.PKG.GITHUB.COM/download/package",
                    "https://npm.pkg.github.com./download/package",
                    "https://npｍ.pkg.github.com/download/package",
                    "https://%4Epm%2Epkg%2Egithub%2Ecom/download/package"):
            with self.subTest(github=url):
                lock = copy.deepcopy(self.lock)
                lock["packages"]["node_modules/unmapped-package"] = {"resolved": url}
                with self.assertRaisesRegex(InventoryGap, "^unmapped_private_package$"):
                    locked_versions(lock, (NAME,))

    def test_malformed_origin_is_incomplete_not_silently_public(self):
        for origin in (None, 7, {}, "https://[invalid", "https:", "https://",
                       "https:npm.pkg.github.com/download/package",
                       "https:///npm.pkg.github.com/download/package",
                       "http:///npm.pkg.github.com/download/package",
                       "https://bad host/package", "https://%FF/package",
                       "https://npm.pkg.github.com%00/package",
                       "https://registry.npmjs.org:invalid/package",
                       "https://registry.npmjs.org:99999/package"):
            with self.subTest(origin=origin):
                self.lock["packages"]["node_modules/public-package"] = {"resolved": origin}
                with self.assertRaisesRegex(InventoryGap, "^invalid_locked_origin$"):
                    locked_versions(self.lock, (NAME,))

    def test_missing_required_package_and_conflicting_nested_pin(self):
        with self.assertRaisesRegex(InventoryGap, "^required_package_missing$"):
            locked_versions({"lockfileVersion": 3, "packages": {}}, (NAME,))
        entry = copy.deepcopy(next(v for k, v in self.lock["packages"].items() if k))
        entry["integrity"] = integrity(b"other")
        self.lock["packages"]["node_modules/" + NAME] = entry
        with self.assertRaisesRegex(InventoryGap, "^conflicting_lock_integrity$"):
            locked_versions(self.lock, (NAME,))

    def test_lock_registry_mismatch_prevents_archive_download(self):
        self.reader.rows["0.5.3"]["dist.integrity"] = integrity(b"changed")
        with self.assertRaisesRegex(InventoryGap, "^lock_registry_integrity_mismatch$"):
            self.run_collect()
        self.assertFalse(any(c[0] == "pack" for c in self.reader.calls))

    def test_catalogue_inconsistency_unknown_origin_and_limits_fail_closed(self):
        cases = (
            (lambda r: r.tags.update(latest="9.9.9"), "invalid_dist_tags"),
            (lambda r: r.rows["0.5.3"].update({"dist.tarball": "https://elsewhere/archive"}),
             "unexpected_archive_origin"),
            (lambda r: r.rows["0.5.3"].update({"dist.unpackedSize": True}),
             "invalid_size_metadata"),
        )
        for mutate, code in cases:
            with self.subTest(code=code):
                reader = FakeReader(self.root)
                mutate(reader)
                with self.assertRaisesRegex(InventoryGap, "^" + code + "$"):
                    self.run_collect(reader)

    def test_changed_tags_during_capture_do_not_produce_complete_report(self):
        calls = 0
        def changed(args, cwd):
            nonlocal calls
            if args == ["view", NAME, "versions", "dist-tags"]:
                calls += 1
                if calls == 2:
                    self.reader.tags["next"] = "0.5.3"
            return self.reader(args, cwd)
        with self.assertRaisesRegex(InventoryGap, "^catalogue_changed_during_capture$"):
            self.run_collect(changed)

    def test_archive_filename_identity_size_and_hash_are_independently_verified(self):
        for field, value, code in (
            ("filename", "../escape.tgz", "invalid_archive_filename"),
            ("name", "@other/package", "archive_identity_mismatch"),
            ("size", 1, "archive_size_mismatch"),
            ("integrity", integrity(b"other"), "archive_integrity_mismatch"),
        ):
            with self.subTest(field=field):
                def altered(args, cwd):
                    rows = self.reader(args, cwd)
                    rows[0][field] = value
                    return rows
                with self.assertRaisesRegex(InventoryGap, "^" + code + "$"):
                    verify_archive(altered, NAME, "0.5.3", integrity(
                        self.reader.bytes["0.5.3"]), self.root)

    def test_safe_cli_failure_does_not_emit_path_or_provider_text(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            result = main(["--scope", "proteinaco", "--lockfile", "/private/missing"])
        self.assertEqual(result, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "DATA_GAP")
        self.assertNotIn("/private", output.getvalue())

    def test_sri_rejects_short_invalid_and_other_algorithm(self):
        for value in (None, "sha1-abcd", "sha512-!", "sha512-YQ=="):
            with self.subTest(value=value), self.assertRaises(InventoryGap):
                sri(value)

    def test_singleton_and_all_prerelease_catalogues(self):
        for version in ("0.5.3", "0.6.0-beta.1"):
            with self.subTest(version=version):
                def singleton(args, cwd):
                    if args[1] == NAME:
                        return {"versions": version, "dist-tags": {"latest": version}}
                    return self.reader.rows[version]
                result = catalogue(singleton, NAME, self.root)
                self.assertEqual(list(result["versions"]), [version])

    def test_catalogue_oversize_duplicate_and_missing_metadata(self):
        for bad in (
            {"versions": ["0.5.3"] * 1001, "dist-tags": {}},
            {"versions": ["0.5.3", "0.5.3"], "dist-tags": {}},
            {"versions": ["not-a-version"], "dist-tags": {}},
        ):
            with self.subTest(bad=bad["versions"][:2]):
                with self.assertRaisesRegex(InventoryGap, "^invalid_version_catalogue$"):
                    catalogue(lambda *args: bad, NAME, self.root)
        def missing(args, cwd):
            if args[1] == NAME:
                return {"versions": ["0.5.3"], "dist-tags": {}}
            return [] if args[1].endswith("@*") else {}
        with self.assertRaisesRegex(InventoryGap, "^missing_version_metadata$"):
            catalogue(missing, NAME, self.root)

    def test_core_requires_both_mapped_packages_and_retains_multiple_locked_versions(self):
        names = SCOPES["core"][1]
        packages = {}
        for name in names:
            for version, parent in (("1.0.0", ""), ("2.0.0", "node_modules/parent/")):
                packages[parent + "node_modules/" + name] = {
                    "version": version, "resolved": REGISTRY + "/download/" + version,
                    "integrity": integrity(version.encode())}
        pins = locked_versions({"lockfileVersion": 2, "packages": packages}, names)
        self.assertEqual(set(pins), set(names))
        self.assertTrue(all(set(versions) == {"1.0.0", "2.0.0"} for versions in pins.values()))

    def test_archive_symlink_and_changed_bytes_are_not_accepted(self):
        target = self.root / "package.tgz"
        outside = self.root / "outside"
        outside.write_bytes(self.reader.bytes["0.5.3"])
        def linked(args, cwd):
            row = self.reader(args, cwd)
            target.unlink()
            target.symlink_to(outside)
            return row
        with self.assertRaisesRegex(InventoryGap, "^invalid_archive_file$"):
            verify_archive(linked, NAME, "0.5.3", integrity(outside.read_bytes()), self.root)
        target.unlink()
        def changed(args, cwd):
            row = self.reader(args, cwd)
            data = target.read_bytes()
            target.write_bytes(bytes([data[0] ^ 1]) + data[1:])
            return row
        with self.assertRaisesRegex(InventoryGap, "^archive_integrity_mismatch$"):
            verify_archive(changed, NAME, "0.5.3", integrity(outside.read_bytes()), self.root)

    def test_workspace_limit_is_an_error(self):
        (self.root / "bytes").write_bytes(b"1234")
        with patch("package_inventory.DISK_LIMIT", 1):
            with self.assertRaisesRegex(InventoryGap, "^npm_workspace_limit$"):
                NpmReader(self.root).disk_guard()

    def test_child_deadline_output_limit_and_failure_never_expose_stderr(self):
        # Real subprocess pipes/deadlines, a fixed Python command instead of npm.
        native_popen = __import__("subprocess").Popen
        scripts = (
            ("import time; time.sleep(2)", "npm_deadline", 0.05),
            ("import sys; sys.stderr.write('private-provider-text'); sys.exit(1)",
             "npm_read_failed", 5),
            ("import sys; sys.stdout.write('x'*5000000)", "npm_output_limit", 5),
            ("print('not-json')", "npm_invalid_json", 5),
        )
        for source, code, deadline in scripts:
            with self.subTest(code=code):
                def child(args, **kwargs):
                    return native_popen([sys.executable, "-c", source], **kwargs)
                reader = NpmReader(self.root, deadline_seconds=deadline)
                with patch("package_inventory.subprocess.Popen", side_effect=child):
                    with self.assertRaisesRegex(InventoryGap, "^" + code + "$"):
                        reader(["view", NAME], self.root)

    def test_native_npm_catalogue_and_pack_through_owned_registry(self):
        self.assertIsNotNone(shutil.which("npm"), "native npm is required for this smoke test")
        payloads = self.reader.bytes
        requests = []
        project_auth = secrets.token_urlsafe(32)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                authorized = self.headers.get("Authorization") == "Bearer " + project_auth
                requests.append(("GET", self.path, authorized))
                if not authorized:
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path.startswith("/tar/"):
                    data = payloads[self.path.split("/")[-1]]
                else:
                    versions = {v: {"name": NAME, "version": v, "dist": {
                        "tarball": origin + "/tar/" + v, "integrity": integrity(payloads[v]),
                        "shasum": hashlib.sha1(payloads[v]).hexdigest(),
                        "unpackedSize": 4096, "fileCount": 1}} for v in VERSIONS}
                    data = json.dumps({"name": NAME, "versions": versions,
                                       "dist-tags": self_tags}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self_tags = copy.deepcopy(self.reader.tags)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        origin = "http://127.0.0.1:" + str(server.server_port)
        (self.root / "package.json").write_text(json.dumps({"name": "owned-test-caller",
                                                           "version": "1.0.0"}))
        # Synthetic project-local configuration references an ephemeral env value;
        # no real caller config is opened and no auth value is written or logged.
        (self.root / ".npmrc").write_text(
            "//127.0.0.1:" + str(server.server_port)
            + "/:_authToken=${NPM_INVENTORY_TEST_AUTH}\n")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        native_popen = __import__("subprocess").Popen
        raw_results = []
        class LocalReader(NpmReader):
            def __init__(self, root, *, project_context):
                super().__init__(root, project_context=project_context)
                # No user/project config or inherited auth in the test subprocess.
                self.env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT") if k in os.environ}
                self.env.update({"HOME": str(root), "npm_config_userconfig": os.devnull,
                                 "npm_config_globalconfig": str(root / "absent-global"),
                                 "npm_config_cache": str(root / "cache"),
                                 "npm_config_logs_max": "0", "npm_config_loglevel": "silent",
                                 "npm_config_update_notifier": "false",
                                 "NPM_INVENTORY_TEST_AUTH": project_auth})
            def __call__(self, args, cwd):
                def local_process(command, **kwargs):
                    # Test transport replaces fixed registry flags only. Production
                    # exposes no registry or insecure-mode option.
                    self_test.assertIn("--ignore-scripts", command)
                    self_test.assertIn("--registry=" + REGISTRY, command)
                    self_test.assertIn("--@merglbot-proteinaco:registry=" + REGISTRY, command)
                    self_test.assertIn(command[1], ("view", "pack"))
                    command = [part.replace(REGISTRY, origin) for part in command]
                    return native_popen(command, **kwargs)
                with patch("package_inventory.subprocess.Popen", side_effect=local_process):
                    value = super().__call__(args, cwd)
                raw_results.append(value)
                if args[0] == "view":
                    value = json.loads(json.dumps(value).replace(origin, REGISTRY))
                return value
        self_test = self
        result = collect("proteinaco", self.lock_path, env=self.env, reader_factory=LocalReader)
        item = result["packages"][NAME]
        self.assertEqual(set(item["versions"]), set(VERSIONS))
        self.assertEqual(item["dist_tags"], self_tags)
        self.assertEqual(item["locked_archives"]["0.5.3"]["integrity"],
                         integrity(payloads["0.5.3"]))
        self.assertEqual(item["locked_archives"]["0.5.3"]["compressed_bytes"],
                         len(payloads["0.5.3"]))
        self.assertTrue(requests)
        self.assertTrue(all(method == "GET" and authorized
                            for method, _, authorized in requests))
        self.assertTrue(any(path == "/tar/0.5.3" for _, path, _ in requests))
        self.assertFalse(list(self.root.glob("*.tgz")))
        before = len(requests)
        class LostProjectContext(LocalReader):
            def __init__(self, root, *, project_context):
                super().__init__(root, project_context=root)
        with self.assertRaisesRegex(InventoryGap, "^npm_read_failed$"):
            collect("proteinaco", self.lock_path, env=self.env,
                    reader_factory=LostProjectContext)
        self.assertTrue(requests[before:])
        self.assertTrue(all(not authorized for _, _, authorized in requests[before:]))


if __name__ == "__main__":
    unittest.main()

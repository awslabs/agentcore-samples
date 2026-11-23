"""Selected API/session binding regressions: real resolver/models, no service clients."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import boto3
import botocore.session
from botocore.serialize import create_serializer
import test_migration
from test_migration import setup, cleanup, ROOT
import oauth_provider as oauth
import policy_contract as c


class SelectedModelTests(unittest.TestCase):
    setUp = test_migration.MigrationTests.setUp
    tearDown = test_migration.MigrationTests.tearDown

    def legacy(self, root, version, home=False):
        data = gzip.decompress((ROOT / 'tests/fixtures/legacy-control-model.json.gz').read_bytes())
        self.assertEqual(hashlib.sha256(data).hexdigest(), '3134fd3d05d47f1c3b3ec5013568d47a01e15c10df8c39e25ec0201b31d844f6')
        model_root = root / '.aws/models' if home else root / 'models'
        path = model_root / c.SERVICE / version / 'service-2.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        return model_root

    def entries(self):
        return ((setup.main, ['setup.py']), (oauth.main, ['oauth_provider.py']),
                (cleanup.main, ['cleanup.py']), (c.main, ['policy_contract.py', 'get-policy',
                 '--policy-engine-id', 'abcdefghijkl', '--policy-id', 'abcdefghijkl', '--region', setup.REGION]))

    def refuse_all(self):
        for entry, argv in self.entries():
            with self.subTest(entry=entry.__module__), patch('sys.argv', argv), \
                 patch.object(boto3, 'Session', side_effect=AssertionError('client construction reached')) as factory:
                with self.assertRaisesRegex(RuntimeError, 'Unsupported|shadowed'):
                    entry()
                factory.assert_not_called()
        client = Mock()
        before = self.config.read_bytes() if self.config.exists() else None
        with self.assertRaisesRegex(RuntimeError, 'Unsupported|shadowed'):
            setup.ensure_base_permits(client, 'abcdefghijkl', 'arn:gateway', {})
        self.assertFalse(client.create_policy.called)
        self.assertEqual(self.config.read_bytes() if self.config.exists() else None, before)

    def test_exact_later_directory_regression_before_clients_and_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = self.legacy(Path(tmp), '2099-01-01')  # synthetic version; unchanged model bytes
            with patch.dict(os.environ, {'AWS_DATA_PATH': str(models)}):
                sdk = botocore.session.Session()
                self.assertEqual(sdk.get_component('data_loader').determine_latest_version(c.SERVICE, 'service-2'), '2099-01-01')
                selected = sdk.get_service_model(c.SERVICE)
                self.assertNotIn('enforcementMode', selected.operation_model('CreatePolicy').input_shape.members)
                with self.assertRaisesRegex(RuntimeError, 'API selection'):
                    c.require_policy_model(sdk)
                self.refuse_all()
                # The actual helper must stop before its default-model serializer or durable write.
                calls = []
                class DefaultModelOnly:
                    def create_policy(self, **params):
                        calls.append(params)
                        create_serializer(selected.protocol).serialize_to_request(params, selected.operation_model('CreatePolicy'))
                with self.assertRaisesRegex(RuntimeError, 'API selection'):
                    setup.ensure_base_permits(DefaultModelOnly(), 'abcdefghijkl', 'arn:gateway', {})
                self.assertEqual(calls, [])
                self.assertFalse(self.config.exists())
                self.config.write_text('{"create_intents":{"original":"keep"}}')
                self.refuse_all()

    def test_same_version_home_and_data_path_shadows_still_refused(self):
        for home in (False, True):
            with self.subTest(home=home), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); models = self.legacy(root, c.API_VERSION, home)
                env = {'HOME': str(root), 'AWS_DATA_PATH': ''} if home else {'AWS_DATA_PATH': str(models)}
                if home:
                    # Loader's CUSTOMER_DATA_PATH is computed at module import.
                    # A genuine HOME-selection probe needs a fresh interpreter,
                    # not a changed HOME after botocore has already imported.
                    code = ("import sys; sys.path.insert(0, " + repr(str(ROOT / 'tests')) + "); "
                            "from test_selected_model import SelectedModelTests; "
                            "case=SelectedModelTests(); case.setUp(); "
                            "case.refuse_all(); case.tearDown()")
                    result = subprocess.run([sys.executable, '-c', code], env={**os.environ, **env},
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    with patch.dict(os.environ, env):
                        self.refuse_all()

    def test_configured_api_versions_follow_real_client_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); models = self.legacy(root, '2099-01-01')
            config = root / 'config'
            with patch.dict(os.environ, {'AWS_DATA_PATH': str(models), 'AWS_CONFIG_FILE': str(config)}):
                for selected in ('2099-01-01', '2098-01-01'):
                    config.write_text('[default]\napi_versions =\n    bedrock-agentcore-control = '+selected+'\n')
                    sdk = botocore.session.Session()
                    self.assertEqual(sdk.get_config_variable('api_versions')[c.SERVICE], selected)
                    with self.assertRaisesRegex(RuntimeError, 'API selection'):
                        c.require_policy_model(sdk)
                    self.refuse_all()
                # An explicit supported selection legitimately takes precedence over latest.
                config.write_text('[default]\napi_versions =\n    bedrock-agentcore-control = '+c.API_VERSION+'\n')
                sdk = botocore.session.Session()
                self.assertEqual(c.require_policy_model(sdk).api_version, c.API_VERSION)
                config.write_text('[default]\napi_versions =\n    s3 = 2099-01-01\n')
                with self.assertRaisesRegex(RuntimeError, 'API selection'):
                    c.require_policy_model(botocore.session.Session())

    def test_verified_session_and_api_are_bound_in_all_entrypoints(self):
        # Genuine session/model is checked; only boto3 client construction is replaced.
        sdk = botocore.session.Session()
        sdk.set_config_variable('api_versions', {c.SERVICE: c.API_VERSION})
        self.assertEqual(c.require_policy_model(sdk).api_version, c.API_VERSION)
        self.config.write_text(json.dumps({'account_id': '123456789012', 'region': setup.REGION, 'role_name': 'owned'}))
        env = {'DISCOVERY_URL': 'https://example.invalid', 'GATEWAY_CLIENT_ID': 'fake',
               'MCP_SERVER_URL': 'https://example.invalid', 'MCP_CREDENTIAL_PROVIDER_ARN': 'arn:fake', 'REGION': setup.REGION}
        for entry, argv in self.entries():
            session = Mock()
            def client(name, **kw):
                if name == c.SERVICE:
                    self.assertEqual(kw['api_version'], c.API_VERSION)
                    raise RuntimeError('VERIFIED_CONTROL_CLIENT_REACHED')
                if name == 'sts':
                    return SimpleNamespace(get_caller_identity=lambda: {'Account': '123456789012'})
                return Mock()
            session.client.side_effect = client
            with self.subTest(entry=entry.__module__), patch.dict(os.environ, env), patch('sys.argv', argv), \
                 patch.object(c.botocore.session, 'Session', return_value=sdk), \
                 patch.object(boto3, 'Session', return_value=session) as factory:
                with self.assertRaisesRegex(RuntimeError, 'VERIFIED_CONTROL_CLIENT_REACHED'):
                    entry()
                self.assertIs(factory.call_args.kwargs['botocore_session'], sdk)
                self.assertEqual(factory.call_args.kwargs['region_name'], setup.REGION)
                factory.assert_called_once()

    def test_package_mismatch_still_stops_every_entrypoint(self):
        original = c.importlib.metadata.version
        for package in c.VERSIONS:
            with self.subTest(package=package), patch.object(c.importlib.metadata, 'version',
                 side_effect=lambda name, package=package: '0.0.0' if name == package else original(name)):
                self.refuse_all()

    def test_direct_helper_rejects_divergent_supplied_sdk_client_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = self.legacy(Path(tmp), c.API_VERSION)
            with patch.dict(os.environ, {'AWS_DATA_PATH': str(models)}):
                legacy = botocore.session.Session().get_service_model(c.SERVICE)
            client = Mock()
            client.meta.service_model = legacy
            with self.assertRaisesRegex(RuntimeError, 'client model'):
                setup.ensure_base_permits(client, 'abcdefghijkl', 'arn:gateway', {})
            client.create_policy.assert_not_called()
            self.assertFalse(self.config.exists())


if __name__ == '__main__':
    unittest.main()

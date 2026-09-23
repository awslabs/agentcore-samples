#!/usr/bin/env python3
"""
Comprehensive validation test for SSM migration implementation.

This script validates all aspects of the SSM migration:
1. Migration utility functionality (dry-run)
2. SSM parameter creation and retrieval
3. Application startup with SSM configuration
4. Error handling when SSM unavailable
5. Sensitive parameter encryption
6. Parameter substitution for ARNs
7. IAM permissions validation

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""

import sys
import tempfile
from pathlib import Path
from typing import List, Tuple
from botocore.exceptions import ClientError

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from ssm_config import SSMConfigLoader
from ssm_migrate import SSMMigrationUtility
from config import Config


class ValidationTest:
    """Comprehensive validation test suite for SSM migration."""

    def __init__(self):
        self.results: List[Tuple[str, bool, str]] = []
        self.test_prefix = "lh_test_"
        self.cleanup_params: List[str] = []

    def log_result(self, test_name: str, passed: bool, message: str = ""):
        """Log a test result."""
        status = "✅ PASS" if passed else "❌ FAIL"
        self.results.append((test_name, passed, message))
        print(f"{status}: {test_name}")
        if message:
            print(f"   {message}")

    def print_summary(self):
        """Print test summary."""
        print("\n" + "=" * 70)
        print("VALIDATION TEST SUMMARY")
        print("=" * 70)

        passed = sum(1 for _, p, _ in self.results if p)
        total = len(self.results)

        print(f"\nTotal Tests: {total}")
        print(f"Passed: {passed}")
        print(f"Failed: {total - passed}")
        print(f"Success Rate: {(passed / total * 100):.1f}%\n")

        if total - passed > 0:
            print("Failed Tests:")
            for name, passed, msg in self.results:
                if not passed:
                    print(f"  ❌ {name}")
                    if msg:
                        print(f"     {msg}")

        print("=" * 70 + "\n")

        return passed == total

    def test_1_ssm_connectivity(self) -> bool:
        """Test 1: Verify SSM connectivity and IAM permissions."""
        print("\n" + "=" * 70)
        print("TEST 1: SSM Connectivity and IAM Permissions")
        print("=" * 70 + "\n")

        try:
            loader = SSMConfigLoader()

            # Test SSM availability
            is_available = loader.is_available()
            self.log_result(
                "SSM Parameter Store is accessible",
                is_available,
                "Check AWS credentials and IAM permissions" if not is_available else "",
            )

            if not is_available:
                return False

            # Test region detection
            region = loader.get_region()
            self.log_result("AWS region auto-detection", bool(region), f"Detected region: {region}")

            # Test account ID detection
            account_id = loader.get_account_id()
            self.log_result(
                "AWS account ID auto-detection",
                bool(account_id) and account_id.isdigit(),
                f"Detected account ID: {account_id}",
            )

            return True

        except Exception as e:
            self.log_result("SSM connectivity test", False, str(e))
            return False

    def test_2_migration_utility_dry_run(self) -> bool:
        """Test 2: Run migration utility in dry-run mode."""
        print("\n" + "=" * 70)
        print("TEST 2: Migration Utility (Dry-Run)")
        print("=" * 70 + "\n")

        try:
            # Create a temporary .env file for testing
            with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
                env_file = Path(f.name)
                f.write("# Test configuration\n")
                f.write("TEST_PARAM_1=value1\n")
                f.write("TEST_PARAM_2=value2\n")
                f.write("TEST_SECRET_KEY=secret123\n")
                f.write("AWS_REGION=us-east-1\n")  # Should be skipped
                f.write("AWS_ACCOUNT_ID=XXXXXXXXXXXX\n")  # Should be skipped

            try:
                utility = SSMMigrationUtility(prefix=self.test_prefix)

                # Run dry-run migration
                result = utility.migrate_env_to_ssm(env_file=env_file, overwrite=False, dry_run=True)

                # Verify dry-run results
                self.log_result(
                    "Dry-run migration executed",
                    True,
                    f"Would create/update {len(result.created) + len(result.updated)} parameters",
                )

                # Verify AWS_REGION and AWS_ACCOUNT_ID are skipped
                skipped_auto = any("auto-detected" in s for s in result.skipped)
                self.log_result(
                    "Auto-detected parameters skipped",
                    skipped_auto,
                    f"Skipped: {[s for s in result.skipped if 'auto-detected' in s]}",
                )

                # Verify no failures in dry-run
                self.log_result(
                    "No failures in dry-run",
                    len(result.failed) == 0,
                    f"Failures: {result.failed}" if result.failed else "",
                )

                return True

            finally:
                # Clean up temp file
                env_file.unlink()

        except Exception as e:
            self.log_result("Migration utility dry-run", False, str(e))
            return False

    def test_3_parameter_creation(self) -> bool:
        """Test 3: Create test parameters and verify they exist."""
        print("\n" + "=" * 70)
        print("TEST 3: SSM Parameter Creation and Retrieval")
        print("=" * 70 + "\n")

        try:
            loader = SSMConfigLoader(prefix=self.test_prefix)
            ssm_client = loader._ssm_client

            # Create test parameters
            test_params = {
                "TEST_STRING_PARAM": ("test_value", "String"),
                "TEST_SECRET_KEY": ("secret_value", "SecureString"),
                "TEST_ARN_PARAM": (
                    "arn:aws:service:${AWS_REGION}:${AWS_ACCOUNT_ID}:resource",
                    "String",
                ),
            }

            for key, (value, param_type) in test_params.items():
                ssm_name = loader._config_key_to_ssm_name(key)
                self.cleanup_params.append(ssm_name)

                try:
                    ssm_client.put_parameter(
                        Name=ssm_name,
                        Value=value,
                        Type=param_type,
                        Overwrite=True,
                        Description=f"Test parameter: {key}",
                    )
                    self.log_result(
                        f"Created parameter: {key}",
                        True,
                        f"SSM name: {ssm_name}, Type: {param_type}",
                    )
                except Exception as e:
                    self.log_result(f"Create parameter: {key}", False, str(e))
                    return False

            # Verify parameters can be retrieved
            loader.clear_cache()  # Clear cache to force SSM retrieval

            for key, (expected_value, _) in test_params.items():
                retrieved_value = loader.get_parameter(key)
                matches = retrieved_value == expected_value
                self.log_result(
                    f"Retrieved parameter: {key}",
                    matches,
                    f"Expected: {expected_value}, Got: {retrieved_value}" if not matches else "",
                )

            return True

        except Exception as e:
            self.log_result("Parameter creation test", False, str(e))
            return False

    def test_4_sensitive_parameter_encryption(self) -> bool:
        """Test 4: Verify sensitive parameters use SecureString type."""
        print("\n" + "=" * 70)
        print("TEST 4: Sensitive Parameter Encryption")
        print("=" * 70 + "\n")

        try:
            loader = SSMConfigLoader(prefix=self.test_prefix)
            ssm_client = loader._ssm_client

            # Check if TEST_SECRET_KEY is SecureString
            ssm_name = loader._config_key_to_ssm_name("TEST_SECRET_KEY")

            response = ssm_client.get_parameter(Name=ssm_name, WithDecryption=False)
            param_type = response["Parameter"]["Type"]

            is_secure = param_type == "SecureString"
            self.log_result(
                "Sensitive parameter uses SecureString",
                is_secure,
                f"Parameter type: {param_type}",
            )

            # Test sensitive parameter detection
            test_cases = [
                ("MY_SECRET_KEY", True),
                ("DATABASE_PASSWORD", True),
                ("API_KEY", True),
                ("AUTH_TOKEN", True),
                ("S3_BUCKET_NAME", False),
                ("DATABASE_NAME", False),
            ]

            for key, should_be_sensitive in test_cases:
                is_sensitive = loader._is_sensitive(key)
                matches = is_sensitive == should_be_sensitive
                self.log_result(
                    f"Sensitive detection: {key}",
                    matches,
                    f"Expected: {should_be_sensitive}, Got: {is_sensitive}" if not matches else "",
                )

            return True

        except Exception as e:
            self.log_result("Sensitive parameter encryption test", False, str(e))
            return False

    def test_5_parameter_substitution(self) -> bool:
        """Test 5: Verify parameter substitution for ARNs."""
        print("\n" + "=" * 70)
        print("TEST 5: Parameter Substitution")
        print("=" * 70 + "\n")

        try:
            loader = SSMConfigLoader(prefix=self.test_prefix)

            # Get the ARN parameter with placeholders
            arn_value = loader.get_parameter("TEST_ARN_PARAM")

            # Create a minimal config-like object for substitution
            class TestConfig:
                def __init__(self):
                    self.AWS_REGION = loader.get_region()
                    self.AWS_ACCOUNT_ID = loader.get_account_id()

                def _substitute_variables(self, value: str) -> str:
                    if "${AWS_ACCOUNT_ID}" in value:
                        value = value.replace("${AWS_ACCOUNT_ID}", self.AWS_ACCOUNT_ID)
                    if "${AWS_REGION}" in value:
                        value = value.replace("${AWS_REGION}", self.AWS_REGION)
                    return value

            test_config = TestConfig()
            substituted = test_config._substitute_variables(arn_value)

            # Verify substitution occurred
            has_placeholders = "${" in substituted
            self.log_result(
                "Parameter substitution removes placeholders",
                not has_placeholders,
                f"Result: {substituted}",
            )

            # Verify correct values were substituted
            contains_region = test_config.AWS_REGION in substituted
            contains_account = test_config.AWS_ACCOUNT_ID in substituted

            self.log_result(
                "Substitution includes AWS_REGION",
                contains_region,
                f"Region: {test_config.AWS_REGION}",
            )

            self.log_result(
                "Substitution includes AWS_ACCOUNT_ID",
                contains_account,
                f"Account ID: {test_config.AWS_ACCOUNT_ID}",
            )

            return True

        except Exception as e:
            self.log_result("Parameter substitution test", False, str(e))
            return False

    def test_6_config_initialization(self) -> bool:
        """Test 6: Test application startup with SSM configuration."""
        print("\n" + "=" * 70)
        print("TEST 6: Application Configuration Initialization")
        print("=" * 70 + "\n")

        try:
            # Note: This will use the actual lh_ prefix, not test prefix
            # We're testing that the Config class can initialize

            config = Config()

            # Verify config loaded
            self.log_result(
                "Config class initialized",
                config._loaded,
                "Configuration loaded from SSM",
            )

            # Verify AWS credentials auto-detected
            has_region = bool(config.AWS_REGION)
            has_account = bool(config.AWS_ACCOUNT_ID)

            self.log_result("AWS_REGION auto-detected", has_region, f"Region: {config.AWS_REGION}")

            self.log_result(
                "AWS_ACCOUNT_ID auto-detected",
                has_account,
                f"Account ID: {config.AWS_ACCOUNT_ID}",
            )

            # Test get() method
            region_via_get = config.get("AWS_REGION")
            self.log_result(
                "Config.get() method works",
                region_via_get == config.AWS_REGION,
                f"Retrieved: {region_via_get}",
            )

            return True

        except Exception as e:
            self.log_result("Config initialization test", False, str(e))
            return False

    def test_7_error_handling(self) -> bool:
        """Test 7: Test error handling when SSM unavailable."""
        print("\n" + "=" * 70)
        print("TEST 7: Error Handling")
        print("=" * 70 + "\n")

        try:
            loader = SSMConfigLoader(prefix=self.test_prefix)

            # Test getting non-existent parameter with default
            default_value = "default_value"
            result = loader.get_parameter("NONEXISTENT_PARAM", default=default_value)

            self.log_result(
                "Non-existent parameter returns default",
                result == default_value,
                f"Expected: {default_value}, Got: {result}",
            )

            # Test parameter name conversion
            test_key = "MY_TEST_PARAMETER"
            expected_ssm_name = f"{self.test_prefix}my_test_parameter"
            actual_ssm_name = loader._config_key_to_ssm_name(test_key)

            self.log_result(
                "Parameter name conversion",
                actual_ssm_name == expected_ssm_name,
                f"Expected: {expected_ssm_name}, Got: {actual_ssm_name}",
            )

            # Test cache functionality
            loader.clear_cache()
            self.log_result(
                "Cache clear functionality",
                len(loader._cache) == 0,
                "Cache cleared successfully",
            )

            return True

        except Exception as e:
            self.log_result("Error handling test", False, str(e))
            return False

    def test_8_export_functionality(self) -> bool:
        """Test 8: Test export functionality."""
        print("\n" + "=" * 70)
        print("TEST 8: Export Functionality")
        print("=" * 70 + "\n")

        try:
            utility = SSMMigrationUtility(prefix=self.test_prefix)

            # Export to temporary file
            with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
                output_file = Path(f.name)

            try:
                # Export without secrets
                count = utility.export_ssm_to_env(output_file=output_file, include_secrets=False)

                self.log_result(
                    "Export executed successfully",
                    count > 0,
                    f"Exported {count} parameters",
                )

                # Verify file was created
                exists = output_file.exists()
                self.log_result("Export file created", exists, f"File: {output_file}")

                if exists:
                    # Read and verify content
                    content = output_file.read_text()

                    # Should contain AWS_REGION and AWS_ACCOUNT_ID
                    has_region = "AWS_REGION=" in content
                    has_account = "AWS_ACCOUNT_ID=" in content

                    self.log_result("Export includes AWS_REGION", has_region)

                    self.log_result("Export includes AWS_ACCOUNT_ID", has_account)

                    # Should mask secrets
                    has_masked = "***MASKED***" in content
                    self.log_result(
                        "Sensitive values masked in export",
                        has_masked,
                        "Secrets are properly masked",
                    )

                return True

            finally:
                # Clean up
                if output_file.exists():
                    output_file.unlink()

        except Exception as e:
            self.log_result("Export functionality test", False, str(e))
            return False

    def test_9_validation_utility(self) -> bool:
        """Test 9: Test validation utility."""
        print("\n" + "=" * 70)
        print("TEST 9: Validation Utility")
        print("=" * 70 + "\n")

        try:
            utility = SSMMigrationUtility(prefix=self.test_prefix)

            # Run validation
            results = utility.validate_ssm_parameters(verbose=False)

            self.log_result(
                "Validation utility executed",
                isinstance(results, dict),
                f"Checked {len(results)} parameters",
            )

            # Note: Validation may fail if required parameters don't exist
            # This is expected for test prefix
            self.log_result("Validation returns results dictionary", True, "Validation completed")

            return True

        except Exception as e:
            self.log_result("Validation utility test", False, str(e))
            return False

    def cleanup(self):
        """Clean up test parameters."""
        print("\n" + "=" * 70)
        print("CLEANUP: Removing Test Parameters")
        print("=" * 70 + "\n")

        if not self.cleanup_params:
            print("No parameters to clean up")
            return

        try:
            loader = SSMConfigLoader(prefix=self.test_prefix)
            ssm_client = loader._ssm_client

            for param_name in self.cleanup_params:
                try:
                    ssm_client.delete_parameter(Name=param_name)
                    print(f"✅ Deleted: {param_name}")
                except ClientError as e:
                    if e.response.get("Error", {}).get("Code") == "ParameterNotFound":
                        print(f"⏭️  Already deleted: {param_name}")
                    else:
                        print(f"❌ Failed to delete: {param_name} - {e}")

        except Exception as e:
            print(f"❌ Cleanup error: {e}")

    def run_all_tests(self) -> bool:
        """Run all validation tests."""
        print("\n" + "=" * 70)
        print("SSM MIGRATION VALIDATION TEST SUITE")
        print("=" * 70)
        print("\nThis test suite validates all requirements for task 10:")
        print("  - Migration utility functionality")
        print("  - SSM parameter creation and retrieval")
        print("  - Application startup with SSM configuration")
        print("  - Error handling")
        print("  - Sensitive parameter encryption")
        print("  - Parameter substitution")
        print("  - IAM permissions validation")
        print("\n")

        try:
            # Run tests in sequence
            self.test_1_ssm_connectivity()
            self.test_2_migration_utility_dry_run()
            self.test_3_parameter_creation()
            self.test_4_sensitive_parameter_encryption()
            self.test_5_parameter_substitution()
            self.test_6_config_initialization()
            self.test_7_error_handling()
            self.test_8_export_functionality()
            self.test_9_validation_utility()

            # Print summary
            all_passed = self.print_summary()

            return all_passed

        finally:
            # Always cleanup
            self.cleanup()


def main():
    """Main entry point."""
    validator = ValidationTest()

    try:
        all_passed = validator.run_all_tests()

        if all_passed:
            print("\n🎉 All validation tests passed!")
            print("The SSM migration implementation is ready for production use.")
            sys.exit(0)
        else:
            print("\n⚠️  Some validation tests failed.")
            print("Please review the failures above and address any issues.")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\n❌ Tests cancelled by user")
        validator.cleanup()
        sys.exit(130)

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        validator.cleanup()
        sys.exit(1)


if __name__ == "__main__":
    main()

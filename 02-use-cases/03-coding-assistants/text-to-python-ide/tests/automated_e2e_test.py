#!/usr/bin/env python3
"""
Automated End-to-End Test Suite for AgentCore Code Interpreter
Runs completely automated without user input
"""

import os
import sys
import subprocess
import time
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))
sys.path.append(str(project_root / "backend"))


class AutomatedE2ETest:
    def __init__(self):
        self.backend_process = None
        self.frontend_process = None
        self.test_results = []
        self.start_time = time.time()

    def log_result(self, test_name, passed, details=""):
        """Log test result"""
        status = "✅ PASS" if passed else "❌ FAIL"
        elapsed = time.time() - self.start_time
        print(f"[{elapsed:.1f}s] {status} {test_name}")
        if details and not passed:
            print(f"    Details: {details}")

        self.test_results.append({"name": test_name, "passed": passed, "details": details, "elapsed": elapsed})
        return passed

    def start_backend(self):
        """Start backend server"""
        print("🚀 Starting backend server...")

        # Kill existing processes
        os.system("lsof -ti:8000 | xargs kill -9 2>/dev/null || true")
        time.sleep(2)

        # Start backend
        env = os.environ.copy()
        env["PYTHONPATH"] = str(project_root)

        self.backend_process = subprocess.Popen(
            [sys.executable, "backend/main.py"],
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        # Wait for backend to start
        for i in range(30):
            try:
                response = requests.get("http://localhost:8000/health", timeout=2)
                if response.status_code == 200:
                    return self.log_result("Backend Startup", True)
            except:
                time.sleep(1)

        return self.log_result("Backend Startup", False, "Backend failed to start within 30 seconds")

    def test_health_endpoint(self):
        """Test health endpoint"""
        try:
            response = requests.get("http://localhost:8000/health", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return self.log_result(
                    "Health Endpoint", data.get("status") == "healthy", f"Status: {data.get('status')}"
                )
            else:
                return self.log_result("Health Endpoint", False, f"Status code: {response.status_code}")
        except Exception as e:
            return self.log_result("Health Endpoint", False, str(e))

    def test_agents_status(self):
        """Test agents status endpoint"""
        try:
            response = requests.get("http://localhost:8000/api/agents/status", timeout=10)
            if response.status_code == 200:
                data = response.json()
                return self.log_result(
                    "Agents Status", data.get("total", 0) > 0, f"Model: {data.get('current_model', 'Unknown')}"
                )
            else:
                return self.log_result("Agents Status", False, f"Status code: {response.status_code}")
        except Exception as e:
            return self.log_result("Agents Status", False, str(e))

    def test_code_generation(self):
        """Test code generation API"""
        try:
            test_prompt = "Create a function to calculate the factorial of a number using recursion"
            response = requests.post(
                "http://localhost:8000/api/generate-code", json={"prompt": test_prompt}, timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                code = data.get("code", "")

                # Validate response
                if not isinstance(code, str):
                    return self.log_result("Code Generation", False, f"Code is not string: {type(code)}")

                if len(code.strip()) == 0:
                    return self.log_result("Code Generation", False, "Generated code is empty")

                # Check if code contains expected elements
                code_lower = code.lower()
                has_function = "def " in code_lower
                has_factorial = "factorial" in code_lower

                if has_function and has_factorial:
                    return self.log_result("Code Generation", True, f"Generated {len(code)} chars")
                else:
                    return self.log_result("Code Generation", False, "Code doesn't contain expected elements")
            else:
                return self.log_result("Code Generation", False, f"Status code: {response.status_code}")

        except Exception as e:
            return self.log_result("Code Generation", False, str(e))

    def test_code_execution(self):
        """Test code execution API"""
        try:
            test_code = """
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

# Test the function
result = fibonacci(10)
print(f"Fibonacci(10) = {result}")
"""

            response = requests.post(
                "http://localhost:8000/api/execute-code", json={"code": test_code.strip()}, timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                result = data.get("result", "")

                # Validate response
                if not isinstance(result, str):
                    return self.log_result("Code Execution", False, f"Result is not string: {type(result)}")

                if len(result.strip()) == 0:
                    return self.log_result("Code Execution", False, "Execution result is empty")

                # Check if result contains expected output
                if "55" in result or "Fibonacci" in result:
                    return self.log_result("Code Execution", True, f"Result: {result[:50]}...")
                else:
                    return self.log_result("Code Execution", False, f"Unexpected result: {result[:100]}")
            else:
                return self.log_result("Code Execution", False, f"Status code: {response.status_code}")

        except Exception as e:
            return self.log_result("Code Execution", False, str(e))

    def test_performance_metrics(self):
        """Test performance with concurrent requests"""
        try:

            def make_request():
                start = time.time()
                response = requests.post(
                    "http://localhost:8000/api/generate-code",
                    json={"prompt": "Create a simple hello world function"},
                    timeout=15,
                )
                elapsed = time.time() - start
                return response.status_code == 200, elapsed

            # Test concurrent requests
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(make_request) for _ in range(3)]
                results = [future.result() for future in as_completed(futures)]

            successful = sum(1 for success, _ in results if success)
            avg_time = sum(elapsed for _, elapsed in results) / len(results)

            if successful >= 2 and avg_time < 20:  # At least 2/3 successful, under 20s average
                return self.log_result("Performance Test", True, f"{successful}/3 successful, avg {avg_time:.1f}s")
            else:
                return self.log_result("Performance Test", False, f"{successful}/3 successful, avg {avg_time:.1f}s")

        except Exception as e:
            return self.log_result("Performance Test", False, str(e))

    def test_error_handling(self):
        """Test error handling"""
        try:
            # Test invalid code execution
            response = requests.post(
                "http://localhost:8000/api/execute-code", json={"code": "invalid_function_call()"}, timeout=15
            )

            if response.status_code == 200:
                data = response.json()
                result = data.get("result", "")

                # Should contain error information
                if "error" in result.lower() or "exception" in result.lower():
                    return self.log_result("Error Handling", True, "Properly handled execution error")
                else:
                    return self.log_result("Error Handling", False, "Error not properly reported")
            else:
                return self.log_result("Error Handling", False, f"Status code: {response.status_code}")

        except Exception as e:
            return self.log_result("Error Handling", False, str(e))

    def cleanup(self):
        """Cleanup processes"""
        print("\n🧹 Cleaning up...")

        if self.backend_process:
            try:
                self.backend_process.terminate()
                self.backend_process.wait(timeout=5)
            except:
                try:
                    self.backend_process.kill()
                except:
                    pass

        # Kill any remaining processes
        os.system("lsof -ti:8000 | xargs kill -9 2>/dev/null || true")
        os.system("lsof -ti:3000 | xargs kill -9 2>/dev/null || true")

    def run_all_tests(self):
        """Run all automated tests"""
        print("🎯 AgentCore Code Interpreter - Automated E2E Test Suite")
        print("=" * 70)

        try:
            # Start backend
            if not self.start_backend():
                print("❌ Cannot continue without backend")
                return False

            # Run tests in sequence
            tests = [
                ("Health Check", self.test_health_endpoint),
                ("Agents Status", self.test_agents_status),
                ("Code Generation", self.test_code_generation),
                ("Code Execution", self.test_code_execution),
                ("Error Handling", self.test_error_handling),
                ("Performance", self.test_performance_metrics),
            ]

            for test_name, test_func in tests:
                print(f"\n📋 Running {test_name}...")
                test_func()

            # Calculate results
            passed = sum(1 for result in self.test_results if result["passed"])
            total = len(self.test_results)
            total_time = time.time() - self.start_time

            print("\n" + "=" * 70)
            print(f"🎯 TEST RESULTS: {passed}/{total} tests passed in {total_time:.1f}s")

            if passed == total:
                print("🎉 All tests passed! Application is working correctly.")
                return True
            else:
                print("❌ Some tests failed. Check the output above.")
                failed_tests = [r["name"] for r in self.test_results if not r["passed"]]
                print(f"Failed tests: {', '.join(failed_tests)}")
                return False

        except KeyboardInterrupt:
            print("\n⚠️  Tests interrupted by user")
            return False
        except Exception as e:
            print(f"\n❌ Test suite error: {e}")
            return False
        finally:
            self.cleanup()


def main():
    """Main test runner"""
    test_runner = AutomatedE2ETest()
    success = test_runner.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

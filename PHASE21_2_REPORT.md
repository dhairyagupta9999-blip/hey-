# Hey Jev Phase 21.2: Native Watcher Cancellation Hardening Report

## Executive Summary

Phase 21.2 hardens the cancellation lifecycle and overlapped I/O management in `WindowsNativeDirectoryWatcher`. 

Prior to this phase, `stop()` invoked `win32file.CancelIo()`. As documented by Microsoft, `CancelIo` only cancels asynchronous I/O initiated by the **calling thread**. Because `ReadDirectoryChangesW` is issued by the watcher's background worker thread (`Watcher_<id>`) while `stop()` is called by the application, test, or coordinator thread, `CancelIo` was a no-op across threads. 

In Phase 21.2:
1. Cross-thread cancellation was audited and implemented using `kernel32.CancelIoEx` via `ctypes`.
2. Dedicated shutdown event signaling (`_h_stop_event`) and `WaitForMultipleObjects` coordination were introduced.
3. The overlapped I/O lifecycle was made strictly leak- and crash-proof: the `OVERLAPPED` structure and read buffer remain alive until completion/abortion is confirmed via `GetOverlappedResult`.
4. Native Win32 handles are never closed while the worker thread remains active.
5. If shutdown times out, `stop()` accurately marks `WatcherState.FAILED`, reports `shutdown_timed_out=True`, and preserves open handles rather than claiming clean release.
6. A comprehensive 8-test Windows test suite (`test_phase21_2.py`) was implemented and verified.
7. Full regression suite passed (589 passed in 247s) and preflight passed with 11 pass, 0 fail, 3 warn.

---

## 1. Cancellation Ownership Audit

### 1.1 Thread Topology
* **Worker Thread (`Watcher_<id>`)**: Created by `BaseWatcher.start()`. Executes `_watch_loop()`, calls Win32 `ReadDirectoryChangesW()` on `self._dir_handle` with `self._overlapped`, and waits on kernel events.
* **Calling Thread**: Main application thread, runtime manager, or test runner. Invokes `watcher.stop()`.

### 1.2 Win32 API Limitation: `CancelIo` vs `CancelIoEx`
* **`win32file.CancelIo(handle)`**: Microsoft documentation explicitly states:
  > *"Cancels all pending input and output (I/O) operations that are started by the calling thread for the specified file."*
  Because the calling thread never issued `ReadDirectoryChangesW`, `CancelIo` failed to cancel the background worker's pending I/O.
* **`CancelIoEx(handle, lpOverlapped)`**: Microsoft documentation states:
  > *"Marks any outstanding I/O operations for the specified file handle. The function cancels only I/O operations in the current process, regardless of which thread created the I/O operation."*
  Passing `lpOverlapped = NULL` cancels all pending I/O operations on the specified file handle across all threads in the process.
* **pywin32 Gap**: `pywin32.win32file` does not expose `CancelIoEx`.
* **Resolution**: Implemented module-level and class-level helper `_cancel_io_ex(handle, overlapped=None)` targeting `ctypes.windll.kernel32.CancelIoEx`.

---

## 2. Overlapped I/O Lifecycle & Safety Guarantees

### 2.1 Multi-Object Wait & Shutdown Coordination
`WindowsNativeDirectoryWatcher` now allocates two manual-reset Win32 events:
* `self._h_event`: Associated with `self._overlapped.hEvent` for I/O completion.
* `self._h_stop_event`: Dedicated shutdown event signaled immediately when `stop()` is invoked.

The worker thread monitors both using:
```python
wait_res = win32event.WaitForMultipleObjects([self._h_event, self._h_stop_event], False, 200)
```
* If `stop()` is invoked while I/O is pending:
  1. `SetEvent(self._h_stop_event)` is set.
  2. `CancelIoEx(self._dir_handle, None)` is called.
  3. The OS transitions the pending I/O to cancelled, sets status code 995 (`ERROR_OPERATION_ABORTED`), and signals `_h_event`.
  4. Worker thread wakes up, drains the cancelled result via `GetOverlappedResult`, catches error 995 as expected cancellation, and exits `_watch_loop`.
* If `stop()` is invoked before `ReadDirectoryChangesW` is issued:
  The worker thread checks `WaitForSingleObject(self._h_stop_event, 0)` before calling `ReadDirectoryChangesW` and exits immediately.
* If a filesystem event arrives concurrently with cancellation:
  The race is resolved safely; any arrived events are processed, the loop detects `_stop_requested`, drains the operation, and terminates.

### 2.2 Buffer and Structure Lifetime Safety
* `self._buffer` (allocated via `win32file.AllocateReadBuffer(8192)`) and `self._overlapped` (`pywintypes.OVERLAPPED()`) are stored as instance attributes.
* Neither is released or set to `None` while the worker thread is running or while any I/O operation is in flight.

### 2.3 Handle Closure and Timeout Integrity
* Native handles (`self._dir_handle`, `self._h_event`, `self._h_stop_event`) are **never closed** until `self._thread.join(timeout=timeout)` confirms the worker thread has completely exited.
* **Timeout Reporting**: If the worker thread does not exit within `timeout` (e.g. simulated uncooperative thread):
  - `stop()` returns `False`.
  - `self.definition.state` is set to `WatcherState.FAILED`.
  - `self._timeout_occurred = True`.
  - `self.last_error` records the exact timeout details.
  - Native handles are **retained** rather than closed to prevent fatal access violations from background access to invalid handles.
* **Idempotency**: Repeated calls to `stop()` check `self._stop_requested` and return `True` safely.

---

## 3. Targeted Windows Native Test Suite

All 8 tests in `test_phase21_2.py` run against real Win32 overlapped I/O on Windows:

| Test Name | Verification Focus | Status |
| :--- | :--- | :--- |
| `test_stop_from_different_thread_while_read_pending` | Cross-thread `CancelIoEx` unblocks worker and cleanly closes handles | **PASS** |
| `test_stop_while_filesystem_change_races_with_cancellation` | High-frequency concurrent writes during `stop()` resolve cleanly without hang or crash | **PASS** |
| `test_repeated_start_stop_cycles_without_handle_or_thread_leak` | 10 rapid start/stop cycles release all threads (`Watcher_*`) and Win32 handles | **PASS** |
| `test_shutdown_with_no_new_filesystem_events` | Idle watcher with zero filesystem events shuts down cleanly with zero false triggers | **PASS** |
| `test_handling_expected_cancellation_error_995` | `ERROR_OPERATION_ABORTED` (995) is handled as nominal shutdown without triggering fallback degradation | **PASS** |
| `test_polling_fallback_behavior_when_native_watching_cannot_initialize` | Unsupported target or failed handle acquisition activates `FilesystemWatcher` fallback | **PASS** |
| `test_stop_idempotency` | Multiple consecutive `stop()` calls return cleanly without error | **PASS** |
| `test_accurate_timeout_reporting_on_hung_thread` | Hung worker thread causes `stop()` to return `False`, set `FAILED`, and retain handles | **PASS** |

---

## 4. Full Revalidation Results

### 4.1 Phase 21, 21.1, and 21.2 Tests
Command: `.venv\Scripts\pytest test_phase21.py test_phase21_1.py test_phase21_2.py -v`
* **Result**: **41 passed in 17.36s**
* **Exit code**: `0`

### 4.2 Full Regression Suite
Command: `.venv\Scripts\pytest -q`
* **Result**: **589 passed in 247.93s (4m 07s)**
* **Baseline**: 581 passed in Phase 21.1 (+8 new Phase 21.2 tests)
* **Exit code**: `0`

### 4.3 Preflight Diagnostics
Command: `.venv\Scripts\python preflight.py`
* **Result**: **11 pass, 0 fail, 3 warn**
* **Expected baseline preserved**: 3 non-blocking warnings (decision mock endpoint, MCP desktop bridge, Phase 12 memory stub).
* **Exit code**: `0`

---

## 5. Security and Scope Compliance
* Zero edits to Google plugin configurations or authentication files (`auth.json`, `config.json`).
* No unapproved Phase 22 feature implementations.
* Clean and scoped modifications confined to `watchers.py` and `test_phase21_2.py`.

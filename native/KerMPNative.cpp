#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <windows.h>
#include <cstdint>
#include <deque>
#include <mutex>
#include <string>

namespace {
constexpr unsigned int kNativeApiVersion = 1u;
constexpr size_t kMaxCaptures = 64;
constexpr size_t kMaxTransactionBytes = 512 * 1024;
struct Capture { std::uint64_t id; std::uint32_t thread_id; std::uint32_t payload_size; std::string payload_hash; };
std::mutex g_mutex;
std::deque<Capture> g_captures;
bool g_initialized = false;
bool g_forwarding_enabled = false;
unsigned int g_dropped_count = 0;
std::string g_last_error = "not_initialized";

HMODULE simulation_module() { return GetModuleHandleW(L"Simulation_x64.dll"); }
bool read_pe_metadata(HMODULE module, DWORD *timestamp, DWORD *image_size) {
    if (!module) return false;
    const auto *base = reinterpret_cast<const BYTE *>(module);
    const auto *dos = reinterpret_cast<const IMAGE_DOS_HEADER *>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return false;
    const auto *nt = reinterpret_cast<const IMAGE_NT_HEADERS *>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return false;
    *timestamp = nt->FileHeader.TimeDateStamp;
    *image_size = nt->OptionalHeader.SizeOfImage;
    return true;
}

PyObject *py_initialize(PyObject *, PyObject *) {
    DWORD timestamp = 0, image_size = 0;
    HMODULE module = simulation_module();
    g_initialized = true;
    // Metadata is diagnostic only. No release-specific offset or signature is
    // accepted as a hook authorization until independently validated live.
    bool supported = false;
    if (!module) g_last_error = "Simulation_x64.dll_not_loaded";
    else if (!supported) g_last_error = "unsupported_build";
    else g_last_error = "capture_boundary_unvalidated";
    Py_RETURN_NONE;
}

PyObject *py_status(PyObject *, PyObject *) {
    std::lock_guard<std::mutex> lock(g_mutex);
    DWORD timestamp = 0, image_size = 0;
    HMODULE module = simulation_module();
    bool metadata_ok = read_pe_metadata(module, &timestamp, &image_size);
    bool supported = false;
    return Py_BuildValue("{s:O,s:O,s:O,s:I,s:I,s:I,s:s,s:s,s:O,s:I,s:O,s:O}",
        "native_loaded", Py_True, "game_build_supported", supported ? Py_True : Py_False,
        "hook_installed", Py_False, "capture_count", static_cast<unsigned int>(g_captures.size()),
        "dropped_count", g_dropped_count, "queue_capacity", static_cast<unsigned int>(kMaxCaptures),
        "last_error", g_last_error.c_str(), "module", "Simulation_x64.dll",
        "fingerprint_metadata_present", metadata_ok ? Py_True : Py_False,
        "native_api_version", kNativeApiVersion, "build_buy_forwarding_enabled", g_forwarding_enabled ? Py_True : Py_False,
        "native_game_build_supported", supported ? Py_True : Py_False);
}

PyObject *py_take_build_operation(PyObject *, PyObject *) {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_captures.empty()) Py_RETURN_NONE;
    Capture capture = g_captures.front(); g_captures.pop_front();
    return Py_BuildValue("{s:K,s:I,s:I,s:s}", "capture_id", static_cast<unsigned long long>(capture.id),
        "thread_id", capture.thread_id, "payload_size", capture.payload_size, "payload_hash", capture.payload_hash.c_str());
}

PyObject *py_set_build_buy_request_forwarding(PyObject *, PyObject *args) {
    int enabled = 0;
    if (!PyArg_ParseTuple(args, "p", &enabled)) return nullptr;
    if (enabled) {
        // There is deliberately no fallback interception path. Enabling is only
        // possible after a validated current-build hook has been added.
        g_forwarding_enabled = false;
        g_last_error = "capture_boundary_unvalidated";
        Py_RETURN_FALSE;
    }
    g_forwarding_enabled = false;
    Py_RETURN_TRUE;
}

PyObject *py_take_build_buy_request(PyObject *, PyObject *) { Py_RETURN_NONE; }

PyObject *py_get_build_buy_request_state(PyObject *, PyObject *) {
    return Py_BuildValue("{s:I,s:O,s:I,s:I,s:s}", "native_api_version", kNativeApiVersion,
        "forwarding_enabled", g_forwarding_enabled ? Py_True : Py_False,
        "queue_capacity", static_cast<unsigned int>(kMaxCaptures), "pending_count", 0u,
        "last_error", g_last_error.c_str());
}

PyObject *py_submit_remote_build_buy_request(PyObject *, PyObject *args) {
    const char *payload = nullptr; Py_ssize_t length = 0;
    if (!PyArg_ParseTuple(args, "y#", &payload, &length)) return nullptr;
    if (length < 0 || static_cast<size_t>(length) > kMaxTransactionBytes) {
        g_last_error = "transaction_payload_too_large";
        Py_RETURN_FALSE;
    }
    // Submission on an arbitrary Python/network thread would be unsafe. A
    // validated Sims main-thread dispatch boundary is required before this can
    // return success.
    g_last_error = "submission_boundary_unvalidated";
    Py_RETURN_FALSE;
}

PyObject *py_take_build_buy_result(PyObject *, PyObject *) { Py_RETURN_NONE; }

PyObject *py_complete_forwarded_build_buy_request(PyObject *, PyObject *args) {
    const char *request_id = nullptr;
    if (!PyArg_ParseTuple(args, "s", &request_id)) return nullptr;
    g_last_error = "completion_boundary_unvalidated";
    Py_RETURN_FALSE;
}

PyMethodDef kMethods[] = {
    {"initialize", py_initialize, METH_NOARGS, "Fingerprint the loaded Sims module; never installs an unvalidated hook."},
    {"status", py_status, METH_NOARGS, "Return bounded native loader and capture status."},
    {"take_build_operation", py_take_build_operation, METH_NOARGS, "Remove and return the oldest copied capture, or None."},
    {"set_build_buy_request_forwarding", py_set_build_buy_request_forwarding, METH_VARARGS, "Enable only a validated current-build forwarding hook."},
    {"take_build_buy_request", py_take_build_buy_request, METH_NOARGS, "Return an intercepted opaque request, or None."},
    {"get_build_buy_request_state", py_get_build_buy_request_state, METH_NOARGS, "Return bounded forwarding diagnostics."},
    {"submit_remote_build_buy_request", py_submit_remote_build_buy_request, METH_VARARGS, "Submit only through a validated main-thread boundary."},
    {"take_build_buy_result", py_take_build_buy_result, METH_NOARGS, "Return an authoritative opaque result, or None."},
    {"complete_forwarded_build_buy_request", py_complete_forwarded_build_buy_request, METH_VARARGS, "Complete a forwarded request after a validated result."},
    {nullptr, nullptr, 0, nullptr}
};
PyModuleDef kModule = {PyModuleDef_HEAD_INIT, "KerMPNative", nullptr, -1, kMethods};
}
PyMODINIT_FUNC PyInit_KerMPNative() { return PyModule_Create(&kModule); }

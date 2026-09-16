#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <windows.h>
#include <cstdint>
#include <deque>
#include <mutex>
#include <string>

namespace {
constexpr DWORD kSupportedTimeDateStamp = 1785275595u;
constexpr DWORD kSupportedImageSize = 17686528u;
constexpr size_t kMaxCaptures = 64;
struct Capture { std::uint64_t id; std::uint32_t thread_id; std::uint32_t payload_size; std::string payload_hash; };
std::mutex g_mutex;
std::deque<Capture> g_captures;
bool g_initialized = false;
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
    bool supported = read_pe_metadata(module, &timestamp, &image_size) && timestamp == kSupportedTimeDateStamp && image_size == kSupportedImageSize;
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
    bool supported = metadata_ok && timestamp == kSupportedTimeDateStamp && image_size == kSupportedImageSize;
    return Py_BuildValue("{s:O,s:O,s:O,s:I,s:I,s:I,s:s,s:s,s:O}",
        "native_loaded", Py_True, "game_build_supported", supported ? Py_True : Py_False,
        "hook_installed", Py_False, "capture_count", static_cast<unsigned int>(g_captures.size()),
        "dropped_count", 0u, "queue_capacity", static_cast<unsigned int>(kMaxCaptures),
        "last_error", g_last_error.c_str(), "module", "Simulation_x64.dll",
        "fingerprint_metadata_present", metadata_ok ? Py_True : Py_False);
}

PyObject *py_take_build_operation(PyObject *, PyObject *) {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_captures.empty()) Py_RETURN_NONE;
    Capture capture = g_captures.front(); g_captures.pop_front();
    return Py_BuildValue("{s:K,s:I,s:I,s:s}", "capture_id", static_cast<unsigned long long>(capture.id),
        "thread_id", capture.thread_id, "payload_size", capture.payload_size, "payload_hash", capture.payload_hash.c_str());
}

PyMethodDef kMethods[] = {
    {"initialize", py_initialize, METH_NOARGS, "Fingerprint the loaded Sims module; never installs an unvalidated hook."},
    {"status", py_status, METH_NOARGS, "Return bounded native loader and capture status."},
    {"take_build_operation", py_take_build_operation, METH_NOARGS, "Remove and return the oldest copied capture, or None."},
    {nullptr, nullptr, 0, nullptr}
};
PyModuleDef kModule = {PyModuleDef_HEAD_INIT, "KerMPNative", nullptr, -1, kMethods};
}
PyMODINIT_FUNC PyInit_KerMPNative() { return PyModule_Create(&kModule); }

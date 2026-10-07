// Windows-only measurement helper. Assign the suspended child before executing it.
#define NOMINMAX
#include <windows.h>
#include <psapi.h>
#include <iostream>
#include <string>

int wmain(int argc,wchar_t** argv) {
    if(argc!=3 && argc!=4) { std::cerr << "memory_probe LIMIT_BYTES COMMAND_LINE [TIMEOUT_MS]\n"; return 2; }
    SIZE_T limit;
    DWORD timeout=120000;
    try {
        if(argv[1][0]<'0' || argv[1][0]>'9') return 2;
        std::size_t used=0;
        auto parsed=std::stoull(argv[1],&used);
        if(used!=std::wstring(argv[1]).size() || parsed>static_cast<unsigned long long>(SIZE_MAX)) return 2;
        limit=static_cast<SIZE_T>(parsed);
        if(argc==4) {
            used=0; parsed=std::stoull(argv[3],&used);
            if(used!=std::wstring(argv[3]).size() || !parsed || parsed>=INFINITE) return 2;
            timeout=static_cast<DWORD>(parsed);
        }
    } catch(...) { return 2; }
    HANDLE job=CreateJobObjectW(nullptr,nullptr);
    PROCESS_INFORMATION process{};
    auto cleanup=[&] {
        if(process.hThread) CloseHandle(process.hThread);
        if(job) CloseHandle(job); // KILL_ON_JOB_CLOSE also handles parent error/termination.
        if(process.hProcess) CloseHandle(process.hProcess);
    };
    auto failure=[&](const char* reason) {
        DWORD error=GetLastError();
        if(process.hProcess) TerminateProcess(process.hProcess,1);
        cleanup(); std::cerr << reason << ": Win32 " << error << '\n'; return 1;
    };
    if(!job) return failure("CreateJobObject");
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION info{};
    info.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if(limit) info.BasicLimitInformation.LimitFlags|=JOB_OBJECT_LIMIT_PROCESS_MEMORY;
    info.ProcessMemoryLimit=limit;
    if(!SetInformationJobObject(job,JobObjectExtendedLimitInformation,&info,sizeof(info))) return failure("SetInformationJobObject");
    STARTUPINFOW startup{}; startup.cb=sizeof(startup);
    startup.dwFlags=STARTF_USESTDHANDLES;
    startup.hStdInput=GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput=GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError=GetStdHandle(STD_ERROR_HANDLE);
    std::wstring command=argv[2];
    if(!CreateProcessW(nullptr,command.data(),nullptr,nullptr,TRUE,CREATE_SUSPENDED|CREATE_NO_WINDOW,nullptr,nullptr,&startup,&process)) return failure("CreateProcess");
    if(!AssignProcessToJobObject(job,process.hProcess)) return failure("AssignProcessToJobObject");
    if(ResumeThread(process.hThread)==DWORD(-1)) return failure("ResumeThread");
    if(WaitForSingleObject(process.hProcess,timeout)!=WAIT_OBJECT_0) return failure("Child wait/timeout");
    DWORD code=1;
    PROCESS_MEMORY_COUNTERS memory{}; memory.cb=sizeof(memory);
    if(!GetExitCodeProcess(process.hProcess,&code) || !GetProcessMemoryInfo(process.hProcess,&memory,sizeof(memory)) ||
       !QueryInformationJobObject(job,JobObjectExtendedLimitInformation,&info,sizeof(info),nullptr)) return failure("Query child counters");
    std::cerr << "memory_probe:{\"limit_committed_bytes\":" << limit << ",\"peak_committed_bytes\":" << info.PeakProcessMemoryUsed
              << ",\"peak_working_set_bytes\":" << memory.PeakWorkingSetSize << ",\"child_exit_code\":" << code << "}\n";
    cleanup(); return static_cast<int>(code);
}

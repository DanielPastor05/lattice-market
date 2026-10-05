// Exercise the production readers without a second implementation or parser API.
#define LATTICE_NO_MAIN
#include "../src/main.cpp"
#include <cstdlib>
#include <unistd.h>

namespace {
constexpr std::size_t max_input=4*1024*1024;
fs::path scratch() {
    // One directory per fuzzer process; libFuzzer calls this target sequentially.
    static const fs::path root=[] {
        std::string pattern=(fs::temp_directory_path()/"lattice-fuzz-XXXXXX").string();
        char* path=mkdtemp(pattern.data());
        if(!path) throw Error(4,"cannot create fuzz directory");
        return fs::path(path);
    }();
    return root/"2026-09-09.lmc";
}
template<class F> void expected_error(F run) {
    try { run(); }
    catch(const Error& error) { if(error.code!=3 && error.code!=5) throw; }
}
void scan(const Bytes& bytes) {
    auto path=scratch();
    { std::ofstream output(path,std::ios::binary|std::ios::trunc); write(output,bytes); }
    Stats stats;
    Reader reader(path,"ESZ26",stats);
    reader.scan(0,std::numeric_limits<I64>::max(),{true,true,true,true,true,true},false,true,[](const Tick&){});
}
void repair(Bytes& bytes) {
    if(bytes.size()<header_size) return;
    auto header_crc=[&] {
        put(bytes,92,0,4);
        Bytes header(bytes.begin(),bytes.begin()+header_size);
        put(bytes,92,crc(header),4);
    };
    U64 offset=get(bytes,64), length=get(bytes,72);
    if(offset<header_size || offset>bytes.size() || length!=bytes.size()-offset) {
        header_crc(); return;
    }
    auto flags=get(bytes,12,4);
    auto stride=flags==3?row_entry_size:entry_size;
    // Invalid descriptors still reach Reader with repaired metadata checksums.
    if((flags==1 || flags==3) && length%stride==0) {
        U64 total=0;
        bool bounded=true;
        for(U64 entry=offset;entry<bytes.size() && bounded;entry+=stride) {
            for(std::size_t field=0;field<(flags==3?1U:6U);++field) {
                auto at=static_cast<std::size_t>(entry+24+field*24);
                auto begin=get(bytes,at), size=get(bytes,at+8);
                if(begin<header_size || begin>offset || size>offset-begin || size>bytes.size()-total) {
                    bounded=false; break;
                }
                total+=size;
            }
        }
        if(bounded) for(U64 entry=offset;entry<bytes.size();entry+=stride) {
            for(std::size_t field=0;field<(flags==3?1U:6U);++field) {
                auto at=static_cast<std::size_t>(entry+24+field*24);
                auto begin=get(bytes,at), size=get(bytes,at+8);
                Bytes payload(bytes.begin()+static_cast<std::ptrdiff_t>(begin),
                              bytes.begin()+static_cast<std::ptrdiff_t>(begin+size));
                put(bytes,at+16,crc(payload),4);
            }
        }
    }
    Bytes directory(bytes.begin()+static_cast<std::ptrdiff_t>(offset),bytes.end());
    put(bytes,88,crc(directory),4);
    header_crc();
}
}

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data,std::size_t size) {
    if(size>max_input) return 0;
    Bytes bytes(data,data+size);
    expected_error([&] { scan(bytes); });
    // Run independently: a failed raw CRC must not bypass deeper repaired-input checks.
    repair(bytes);
    expected_error([&] { scan(bytes); });
    if(size<=1024*1024) expected_error([&] {
        JsonReader(std::string_view(reinterpret_cast<const char*>(data),size)).parse();
    });
    if(size<=4096) expected_error([&] {
        parse(std::string_view(reinterpret_cast<const char*>(data),size),1);
    });
    return 0;
}

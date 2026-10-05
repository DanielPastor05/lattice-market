#include <algorithm>
#include <array>
#include <bit>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <locale>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace fs = std::filesystem;
using Bytes = std::vector<std::uint8_t>;
using I64 = std::int64_t;
using U64 = std::uint64_t;
constexpr I64 second = 1'000'000'000, day_ns = 86400 * second;
constexpr std::uint32_t capacity = 65536;
constexpr std::size_t header_size = 128, entry_size = 168;
constexpr std::size_t row_entry_size = 48;
struct Error : std::runtime_error {
    int code;
    Error(int c, const std::string& s) : std::runtime_error(s), code(c) {}
};
[[noreturn]] void bad(const std::string& s) { throw Error(3, s); }
U64 add(U64 a, U64 b) {
    if (b > std::numeric_limits<U64>::max() - a) throw Error(5, "integer overflow");
    return a + b;
}
void put(Bytes& b, std::size_t at, U64 v, unsigned n = 8) {
    for (unsigned i = 0; i < n; ++i) b.at(at + i) = static_cast<std::uint8_t>(v >> (8 * i));
}
U64 get(const Bytes& b, std::size_t at, unsigned n = 8) {
    U64 v = 0;
    for (unsigned i = 0; i < n; ++i) v |= static_cast<U64>(b.at(at + i)) << (8 * i);
    return v;
}
I64 signed_bits(U64 v) { return std::bit_cast<I64>(v); }
std::uint32_t crc(const Bytes& b) {
    static const auto table = [] {
        std::array<std::uint32_t, 256> t{};
        for (std::uint32_t i = 0; i < 256; ++i) {
            auto x = i;
            for (int j = 0; j < 8; ++j) x = (x >> 1) ^ ((x & 1) ? 0xedb88320U : 0U);
            t[i] = x;
        }
        return t;
    }();
    std::uint32_t x = 0xffffffffU;
    for (auto c : b) x = table[(x ^ c) & 255] ^ (x >> 8);
    return x ^ 0xffffffffU;
}
void write(std::ostream& f, const Bytes& b) {
    f.write(reinterpret_cast<const char*>(b.data()), static_cast<std::streamsize>(b.size()));
    if (!f) throw Error(4, "write failed");
}
Bytes read(std::ifstream& f, U64 offset, std::size_t size) {
    f.clear(); f.seekg(static_cast<std::streamoff>(offset));
    Bytes b(size);
    f.read(reinterpret_cast<char*>(b.data()), static_cast<std::streamsize>(size));
    if (!f) bad("truncated/unreadable columnar file");
    return b;
}
U64 integer(std::string_view s) {
    U64 v{};
    auto [p, e] = std::from_chars(s.data(), s.data() + s.size(), v);
    if (s.empty() || e != std::errc{} || p != s.data() + s.size()) bad("invalid unsigned integer");
    return v;
}
I64 price(std::string_view s) {
    bool negative = !s.empty() && s.front() == '-';
    if (negative) s.remove_prefix(1);
    auto pos = s.find('.');
    auto whole = integer(s.substr(0, pos));
    unsigned quarter = 0;
    if (pos != std::string_view::npos) {
        auto frac = s.substr(pos + 1);
        if (frac.empty()) bad("empty price fraction");
        for (char c : frac) if (c < '0' || c > '9') bad("invalid price fraction");
        while (frac.size() > 2 && frac.back() == '0') frac.remove_suffix(1);
        if (frac.size() > 2) bad("price is not a multiple of 0.25");
        auto cents = integer(frac) * (frac.size() == 1 ? 10 : 1);
        if (cents % 25 != 0) bad("price is not a multiple of 0.25");
        quarter = static_cast<unsigned>(cents / 25);
    }
    const U64 limit = static_cast<U64>(std::numeric_limits<I64>::max()) + (negative ? 1 : 0);
    if (whole > (limit - quarter) / 4) bad("price overflow");
    U64 value = whole * 4 + quarter;
    if (negative && value == (U64{1} << 63)) return std::numeric_limits<I64>::min();
    return negative ? -static_cast<I64>(value) : static_cast<I64>(value);
}
I64 timestamp(std::string_view s, bool source) {
    int y, m, d, h, minute, sec;
    U64 fraction = 0;
    auto number = [&](std::size_t p, std::size_t n) { return static_cast<int>(integer(s.substr(p, n))); };
    if (source) {
        if (s.size() != 23 || s[8] != ' ' || s[15] != ' ') bad("expected yyyyMMdd HHmmss fffffff");
        y=number(0,4); m=number(4,2); d=number(6,2);
        h=number(9,2); minute=number(11,2); sec=number(13,2);
        fraction=integer(s.substr(16,7))*100;
    } else {
        if (s.size()<20 || s[4]!='-' || s[7]!='-' || s[10]!='T' || s[13]!=':' || s[16]!=':' || s.back()!='Z')
            bad("expected UTC ISO timestamp ending in Z");
        y=number(0,4); m=number(5,2); d=number(8,2);
        h=number(11,2); minute=number(14,2); sec=number(17,2);
        if (s.size()!=20) {
            if (s[19]!='.' || s.size()<22 || s.size()>30) bad("invalid timestamp fraction");
            auto f=s.substr(20,s.size()-21); fraction=integer(f);
            for (std::size_t i=f.size(); i<9; ++i) fraction*=10;
        }
    }
    auto date = std::chrono::year{y}/std::chrono::month{static_cast<unsigned>(m)}/std::chrono::day{static_cast<unsigned>(d)};
    if (y<1970 || y>2100 || !date.ok() || h>23 || minute>59 || sec>59) bad("invalid calendar/time");
    I64 days = std::chrono::sys_days{date}.time_since_epoch().count();
    return days*day_ns + (h*3600LL+minute*60LL+sec)*second + static_cast<I64>(fraction);
}
std::string date_string(I64 day) {
    auto date=std::chrono::year_month_day{std::chrono::sys_days{std::chrono::days{day}}};
    std::ostringstream s;
    s << std::setfill('0') << std::setw(4) << static_cast<int>(date.year()) << '-'
      << std::setw(2) << static_cast<unsigned>(date.month()) << '-' << std::setw(2) << static_cast<unsigned>(date.day());
    return s.str();
}
std::string iso(I64 ns) {
    auto t=ns%day_ns; std::ostringstream s;
    s << date_string(ns/day_ns) << 'T' << std::setfill('0') << std::setw(2) << t/(3600*second)
      << ':' << std::setw(2) << (t/(60*second))%60 << ':' << std::setw(2) << (t/second)%60
      << '.' << std::setw(9) << t%second << 'Z';
    return s.str();
}
struct Tick { U64 row; I64 ns, last, bid, ask; U64 volume; };
Tick parse(std::string_view line, U64 row) {
    if (!line.empty() && line.back()=='\r') line.remove_suffix(1);
    std::array<std::string_view,5> fields{};
    for (int i=0; i<5; ++i) {
        auto p=line.find(';');
        if ((i<4 && p==std::string_view::npos) || (i==4 && p!=std::string_view::npos)) bad("expected five fields");
        fields[i]=line.substr(0,p);
        if (i<4) line.remove_prefix(p+1);
    }
    Tick t{row,timestamp(fields[0],true),price(fields[1]),price(fields[2]),price(fields[3]),integer(fields[4])};
    if (t.last<=0 || t.volume==0) bad("last price and volume must be positive");
    return t;
}
bool line_read(std::ifstream& f, std::string& line) {
    line.clear(); char c;
    while (f.get(c)) {
        if (c=='\n') return true;
        if (line.size()==4096) bad("line exceeds 4096 bytes");
        line.push_back(c);
    }
    if (!f.eof()) throw Error(4,"source read failed");
    return !line.empty();
}
struct Descriptor { U64 offset,length; std::uint32_t checksum; };
struct Block { I64 min,max; std::uint32_t rows; std::array<Descriptor,6> columns; };
Bytes encode(const Block& b,bool row_layout=false) {
    Bytes e(row_layout?row_entry_size:entry_size); put(e,0,static_cast<U64>(b.min)); put(e,8,static_cast<U64>(b.max)); put(e,16,b.rows,4);
    for (std::size_t c=0;c<(row_layout?1U:6U);++c) {
        auto p=24+c*24; put(e,p,b.columns[c].offset); put(e,p+8,b.columns[c].length); put(e,p+16,b.columns[c].checksum,4);
    }
    return e;
}
Block decode(const Bytes& e,bool row_layout=false) {
    Block b{signed_bits(get(e,0)),signed_bits(get(e,8)),static_cast<std::uint32_t>(get(e,16,4)),{}};
    if (get(e,20,4)) bad("nonzero directory reserved field");
    for (std::size_t c=0;c<(row_layout?1U:6U);++c) {
        auto p=24+c*24;
        if (get(e,p+20,4)) bad("nonzero column reserved field");
        b.columns[c]={get(e,p),get(e,p+8),static_cast<std::uint32_t>(get(e,p+16,4))};
    }
    return b;
}
// ponytail: one day directory in memory (~168 bytes/block); disk spooling if daily metadata exceeds the memory budget.
class Writer {
    fs::path root, path;
    std::string contract;
    bool row_layout;
    std::ofstream file;
    I64 current_day=-1;
    U64 rows=0, position=header_size;
    std::vector<Tick> ticks;
    std::vector<Block> blocks;
    void flush() {
        if (ticks.empty()) return;
        Block b{ticks.front().ns,ticks.back().ns,static_cast<std::uint32_t>(ticks.size()),{}};
        Bytes data(ticks.size()*(row_layout?48U:8U));
        for (std::size_t c=0;c<(row_layout?1U:6U);++c) {
            for (std::size_t i=0;i<ticks.size();++i) {
                const auto& t=ticks[i];
                std::array<U64,6> v{t.row,static_cast<U64>(t.ns),static_cast<U64>(t.last),static_cast<U64>(t.bid),static_cast<U64>(t.ask),t.volume};
                if(row_layout) { for(std::size_t field=0;field<6;++field) put(data,i*48+field*8,v[field]); }
                else put(data,i*8,v[c]);
            }
            b.columns[c]={position,static_cast<U64>(data.size()),crc(data)};
            write(file,data); position=add(position,data.size());
        }
        blocks.push_back(b); rows=add(rows,ticks.size()); ticks.clear();
    }
public:
    Writer(fs::path r,std::string c,bool row=false):root(std::move(r)),contract(std::move(c)),row_layout(row) { ticks.reserve(capacity); }
    void finish_block() { flush(); }
    void finish() {
        if (!file.is_open()) return;
        flush(); U64 directory_offset=position;
        auto stride=row_layout?row_entry_size:entry_size;
        Bytes directory(blocks.size()*stride);
        for (std::size_t i=0;i<blocks.size();++i) {
            auto e=encode(blocks[i],row_layout); std::copy(e.begin(),e.end(),directory.begin()+static_cast<std::ptrdiff_t>(i*stride));
        }
        write(file,directory);
        Bytes h(header_size); const std::string magic="LATTICE1";
        std::copy(magic.begin(),magic.end(),h.begin()); put(h,8,1,2); put(h,10,128,2); put(h,12,row_layout?3:1,4);
        std::copy(contract.begin(),contract.end(),h.begin()+16);
        put(h,32,static_cast<U64>(current_day),4); put(h,36,4,4); put(h,40,1,4); put(h,44,capacity,4);
        put(h,48,rows); put(h,56,blocks.size()); put(h,64,directory_offset); put(h,72,directory.size());
        put(h,80,add(directory_offset,directory.size())); put(h,88,crc(directory),4); put(h,92,crc(h),4);
        file.seekp(0); write(file,h); file.close(); if (!file) throw Error(4,"close failed");
        blocks.clear(); rows=0; position=header_size;
    }
    void append(const Tick& t) {
        auto d=t.ns/day_ns;
        if (d!=current_day) {
            finish(); current_day=d; path=root/(date_string(d)+".lmc");
            file.open(path,std::ios::binary|std::ios::trunc);
            if (!file) throw Error(4,"cannot create "+path.string());
            write(file,Bytes(header_size));
        }
        ticks.push_back(t); if (ticks.size()==capacity) flush();
    }
};
struct Stats { U64 files=0, blocks=0, skipped=0, decoded=0, selected=0, bytes=0; };
class Reader {
    std::ifstream file;
    U64 directory_offset=0, block_count=0;
    I64 day=0;
    std::size_t directory_entry_size=entry_size;
    Stats& stats;
public:
    U64 record_count=0;
    bool row_layout=false;
    Reader(const fs::path& p,const std::string& contract,Stats& s):file(p,std::ios::binary),stats(s) {
        if (!file) throw Error(4,"cannot open "+p.string());
        U64 size=fs::file_size(p);
        if (size<header_size) bad("truncated header");
        auto h=read(file,0,128); stats.bytes=add(stats.bytes,128); ++stats.files;
        if (std::string(h.begin(),h.begin()+8)!="LATTICE1" || get(h,8,2)!=1 || get(h,10,2)!=128 || (get(h,12,4)!=1 && get(h,12,4)!=3))
            bad("unsupported file header");
        auto checksum=get(h,92,4); put(h,92,0,4); if (crc(h)!=checksum) bad("header CRC mismatch");
        row_layout=get(h,12,4)==3;
        directory_entry_size=row_layout?row_entry_size:entry_size;
        std::string stored;
        for (std::size_t i=16;i<32;++i) { if (h[i]==0) break; stored+=static_cast<char>(h[i]); }
        if (stored!=contract) bad("contract mismatch");
        for (std::size_t i=16+stored.size();i<32;++i) if (h[i]) bad("invalid contract padding");
        for (std::size_t i=96;i<128;++i) if (h[i]) bad("nonzero header reserved field");
        day=static_cast<I64>(get(h,32,4));
        if (day<0 || day>47846 || p.filename()!=date_string(day)+".lmc") bad("invalid partition day/name");
        if (get(h,36,4)!=4 || get(h,40,4)!=1 || get(h,44,4)!=capacity || get(h,80)!=size) bad("invalid file metadata");
        directory_offset=get(h,64); block_count=get(h,56);
        auto directory_bytes=get(h,72);
        if (!block_count || directory_offset<128 || directory_offset>size || block_count>(size-128)/directory_entry_size ||
            directory_bytes!=block_count*directory_entry_size || directory_bytes!=size-directory_offset) bad("invalid directory bounds");
        // Validate metadata before using min/max to skip any payload.
        U64 count=0,next=128; I64 previous=-1;
        std::uint32_t rolling=0xffffffffU;
        for (U64 i=0;i<block_count;++i) {
            auto e=read(file,directory_offset+i*directory_entry_size,directory_entry_size); stats.bytes=add(stats.bytes,directory_entry_size);
            // CRC update across bounded directory entries (same IEEE polynomial).
            for (auto c:e) {
                rolling^=c;
                for (int bit=0;bit<8;++bit) rolling=(rolling>>1)^((rolling&1)?0xedb88320U:0U);
            }
            auto b=decode(e,row_layout);
            if (!b.rows || b.rows>capacity || b.min>b.max || b.min<day*day_ns || b.max>=(day+1)*day_ns || b.min<previous)
                bad("invalid block ordering/range");
            previous=b.max; count=add(count,b.rows);
            for (std::size_t field=0;field<(row_layout?1U:6U);++field) {
                const auto& c=b.columns[field];
                if (c.offset!=next || c.length!=U64{b.rows}*(row_layout?48U:8U) || c.length>directory_offset-next) bad("invalid payload bounds");
                next+=c.length;
            }
        }
        if ((rolling^0xffffffffU)!=get(h,88,4) || count!=get(h,48) || next!=directory_offset) bad("invalid directory CRC/count");
        record_count=count;
    }
    void scan(I64 from,I64 to,const std::array<bool,6>& needed,bool prune,bool verify,const std::function<void(const Tick&)>& emit,
              const std::function<void()>& block_end={}) {
        U64 previous_row=0; I64 previous_ns=-1;
        for (U64 i=0;i<block_count;++i) {
            auto e=read(file,directory_offset+i*directory_entry_size,directory_entry_size); stats.bytes=add(stats.bytes,directory_entry_size);
            auto b=decode(e,row_layout); ++stats.blocks;
            if (prune && (b.max<from || b.min>=to)) { ++stats.skipped; continue; }
            std::array<Bytes,6> columns;
            for (std::size_t c=0;c<(row_layout?1U:6U);++c) if (row_layout || needed[c]) {
                columns[c]=read(file,b.columns[c].offset,static_cast<std::size_t>(b.columns[c].length));
                stats.bytes=add(stats.bytes,b.columns[c].length);
                if (crc(columns[c])!=b.columns[c].checksum) bad("column CRC mismatch");
            }
            for (std::uint32_t row=0;row<b.rows;++row) {
                auto value=[&](std::size_t c) { return !needed[c]?U64{0}:row_layout?get(columns[0],row*48+c*8):get(columns[c],row*8); };
                Tick t{value(0),signed_bits(value(1)),signed_bits(value(2)),signed_bits(value(3)),signed_bits(value(4)),value(5)};
                ++stats.decoded;
                if (t.ns<b.min || t.ns>b.max || t.ns<previous_ns || (row==0 && t.ns!=b.min) || (row+1==b.rows && t.ns!=b.max))
                    bad("timestamp payload inconsistent with metadata");
                previous_ns=t.ns;
                if ((needed[2] && t.last<=0) || (needed[5] && !t.volume)) bad("invalid price/volume payload");
                if (verify) {
                    if (!t.row || t.row<=previous_row || t.last<=0 || !t.volume) bad("invalid tick payload");
                    previous_row=t.row;
                }
                if (t.ns>=from && t.ns<to) { ++stats.selected; emit(t); }
            }
            if(block_end) block_end();
        }
    }
};
struct Aggregate {
    U64 count=0,volume=0; I64 open=0,high=0,low=0,close=0;
    double sum=0, correction=0;
    void append(const Tick& t) {
        if (!count) open=high=low=t.last;
        high=std::max(high,t.last); low=std::min(low,t.last); close=t.last;
        count=add(count,1); volume=add(volume,t.volume);
        double term=(static_cast<double>(t.last)/4)*static_cast<double>(t.volume);
        double adjusted=term-correction,next=sum+adjusted; correction=(next-sum)-adjusted; sum=next;
        if (!std::isfinite(sum)) throw Error(5,"VWAP overflow");
    }
    double vwap() const { return sum/static_cast<double>(volume); }
};
std::string money(I64 ticks) {
    std::ostringstream out;
    out << ticks/4 << '.' << std::setfill('0') << std::setw(2) << (ticks%4)*25;
    return out.str();
}
using Options=std::map<std::string,std::string>;
struct Json {
    char type=0;
    std::string text;
    std::vector<Json> items;
    std::map<std::string,Json> fields;
};
// Small, bounded JSON reader for provenance manifests; no dependency in the engine.
class JsonReader {
    std::string_view input;
    std::size_t at=0;
    void spaces() { while(at<input.size() && (input[at]==' ' || input[at]=='\n' || input[at]=='\r' || input[at]=='\t')) ++at; }
    char take() { if(at==input.size()) bad("truncated manifest JSON"); return input[at++]; }
    bool consume(char c) { spaces(); if(at<input.size() && input[at]==c) { ++at; return true; } return false; }
    unsigned hex4() {
        unsigned code=0;
        for(int i=0;i<4;++i) {
            char c=take(); unsigned digit;
            if(c>='0' && c<='9') digit=static_cast<unsigned>(c-'0');
            else if(c>='a' && c<='f') digit=static_cast<unsigned>(c-'a'+10);
            else if(c>='A' && c<='F') digit=static_cast<unsigned>(c-'A'+10);
            else { bad("invalid JSON unicode escape"); }
            code=code*16+digit;
        }
        return code;
    }
    std::string string() {
        if(take()!='"') bad("expected JSON string");
        std::string s;
        for(;;) {
            char c=take(); if(c=='"') return s;
            if(static_cast<unsigned char>(c)<32) bad("control character in JSON string");
            if(c!='\\') { s+=c; continue; }
            c=take();
            switch(c) {
                case '"': case '\\': case '/': s+=c; break;
                case 'b': s+='\b'; break; case 'f': s+='\f'; break;
                case 'n': s+='\n'; break; case 'r': s+='\r'; break; case 't': s+='\t'; break;
                case 'u': {
                    unsigned code=hex4();
                    if(code>=0xd800 && code<=0xdbff) {
                        if(take()!='\\' || take()!='u') bad("missing JSON low surrogate");
                        unsigned low=hex4(); if(low<0xdc00 || low>0xdfff) bad("invalid JSON surrogate");
                        code=0x10000+(code-0xd800)*1024+(low-0xdc00);
                    } else if(code>=0xdc00 && code<=0xdfff) bad("unpaired JSON surrogate");
                    if(code<128) s+=static_cast<char>(code);
                    else {
                        if(code>=0x10000) s+=static_cast<char>(0xf0|(code>>18));
                        if(code>=0x800) s+=static_cast<char>((code>=0x10000?0x80:0xe0)|((code>>12)&63));
                        s+=static_cast<char>((code>=0x800?0x80:0xc0)|((code>>6)&63));
                        s+=static_cast<char>(0x80|(code&63));
                    }
                    break;
                }
                default: bad("invalid JSON escape");
            }
        }
    }
    Json value(unsigned depth) {
        if(depth>16) bad("manifest JSON too deeply nested");
        spaces(); if(at==input.size()) bad("empty JSON value");
        Json result; char c=input[at];
        if(c=='"') { result.type='s'; result.text=string(); }
        else if(c=='{' || c=='[') {
            ++at; result.type=c;
            char end=c=='{'?'}':']'; if(consume(end)) return result;
            do {
                if(c=='{') {
                    spaces(); auto key=string(); if(!consume(':')) bad("missing JSON colon");
                    if(!result.fields.emplace(key,value(depth+1)).second) bad("duplicate JSON key");
                } else result.items.push_back(value(depth+1));
                if(consume(end)) return result;
            } while(consume(','));
            bad("invalid JSON separator");
        } else {
            auto begin=at;
            while(at<input.size() && input[at]!=',' && input[at]!='}' && input[at]!=']' && input[at]!=' ' && input[at]!='\n' && input[at]!='\r' && input[at]!='\t') ++at;
            result.text=std::string(input.substr(begin,at-begin));
            if(result.text=="true" || result.text=="false" || result.text=="null") result.type='l';
            else {
                // JSON number grammar, including decimal timing metadata.
                const auto& n=result.text; std::size_t p=0;
                if(p<n.size() && n[p]=='-') ++p;
                if(p==n.size()) bad("invalid JSON number");
                if(n[p]=='0') ++p;
                else { if(n[p]<'1' || n[p]>'9') bad("invalid JSON number"); while(p<n.size() && n[p]>='0' && n[p]<='9') ++p; }
                auto digits=[&] { auto start=p; while(p<n.size() && n[p]>='0' && n[p]<='9') ++p; if(start==p) bad("invalid JSON number"); };
                if(p<n.size() && n[p]=='.') { ++p; digits(); }
                if(p<n.size() && (n[p]=='e' || n[p]=='E')) { ++p; if(p<n.size() && (n[p]=='+' || n[p]=='-')) ++p; digits(); }
                if(p!=n.size()) bad("invalid JSON number"); result.type='n';
            }
        }
        return result;
    }
public:
    explicit JsonReader(std::string_view s):input(s) {}
    Json parse() { auto result=value(0); spaces(); if(at!=input.size()) bad("trailing manifest JSON"); return result; }
};
Options options(int argc,char** argv,int begin,const std::vector<std::string>& allowed) {
    Options o;
    for (int i=begin;i<argc;i+=2) {
        std::string key=argv[i];
        if (i+1>=argc || std::find(allowed.begin(),allowed.end(),key)==allowed.end() || o.contains(key)) throw Error(2,"unknown, duplicate or missing option: "+key);
        o.emplace(key,argv[i+1]);
    }
    return o;
}
std::string required(const Options& o,const std::string& key) {
    auto i=o.find(key); if (i==o.end() || i->second.empty()) throw Error(2,"required option: "+key); return i->second;
}
std::string contract_option(const Options& o) {
    auto c=required(o,"--contract"); if (c!="ESZ26" && c!="NQZ26") throw Error(2,"supported contracts: ESZ26, NQZ26"); return c;
}
std::vector<fs::path> partitions(const fs::path& root,const std::string& contract,U64& expected_count,bool& row_layout) {
    if (!fs::is_directory(root) || !fs::is_regular_file(root/"manifest.json")) throw Error(4,"published contract directory/manifest missing");
    auto size=fs::file_size(root/"manifest.json");
    if(size>1024*1024) bad("manifest exceeds 1 MiB");
    std::ifstream manifest_file(root/"manifest.json",std::ios::binary);
    if(!manifest_file) throw Error(4,"cannot open manifest");
    std::string text(static_cast<std::size_t>(size),'\0');
    manifest_file.read(text.data(),static_cast<std::streamsize>(size)); if(!manifest_file) bad("truncated manifest");
    auto manifest=JsonReader(text).parse();
    if(manifest.type!='{') bad("manifest must be an object");
    auto field=[&](const std::string& name,char type)->const Json& {
        auto i=manifest.fields.find(name);
        if(i==manifest.fields.end() || i->second.type!=type) bad("invalid manifest field: "+name);
        return i->second;
    };
    if(integer(field("schema_version",'n').text)!=1 || field("contract",'s').text!=contract) bad("manifest schema/contract mismatch");
    expected_count=integer(field("record_count",'n').text);
    row_layout=false;
    if(manifest.fields.contains("storage_layout")) {
        auto layout=field("storage_layout",'s').text;
        if(layout!="column" && layout!="row") bad("invalid manifest storage layout");
        row_layout=layout=="row";
    }
    std::vector<std::string> declared;
    for(const auto& item:field("partitions",'[').items) {
        if(item.type!='s' || item.text.size()!=14 || item.text.substr(10)!=".lmc" || fs::path(item.text).filename()!=item.text) bad("invalid manifest partition");
        declared.push_back(item.text);
    }
    std::sort(declared.begin(),declared.end());
    if(declared.empty() || std::adjacent_find(declared.begin(),declared.end())!=declared.end()) bad("empty/duplicate manifest partitions");
    std::vector<fs::path> files;
    for (const auto& e:fs::directory_iterator(root)) if (e.path().extension()==".lmc") files.push_back(e.path());
    std::sort(files.begin(),files.end());
    if(files.size()!=declared.size()) bad("partition inventory differs from manifest");
    for(std::size_t i=0;i<files.size();++i) if(files[i].filename()!=declared[i]) bad("partition inventory differs from manifest");
    return files;
}
void convert_row(const Options& o) {
    auto contract=contract_option(o);
    fs::path source=fs::path(required(o,"--data"))/contract,output=required(o,"--output");
    if(!fs::is_directory(output) || !fs::is_empty(output)) throw Error(4,"conversion output must be an existing empty staging directory");
    U64 expected=0,previous=0; bool declared_row=false;
    auto inputs=partitions(source,contract,expected,declared_row);
    if(declared_row) throw Error(2,"conversion requires a column source");
    Writer writer(output,contract,true); Stats input_stats;
    for(const auto& p:inputs) {
        Reader reader(p,contract,input_stats);
        if(reader.row_layout) bad("manifest/header layout mismatch");
        reader.scan(0,std::numeric_limits<I64>::max(),{true,true,true,true,true,true},false,true,[&](const Tick& t) {
            if(t.row<=previous) bad("source rows regress across partitions");
            previous=t.row; writer.append(t);
        },[&] { writer.finish_block(); });
    }
    writer.finish();
    if(input_stats.selected!=expected) bad("manifest count differs from converted rows");
    Stats output_stats;
    for(const auto& p:fs::directory_iterator(output)) if(p.path().extension()==".lmc") {
        Reader reader(p.path(),contract,output_stats);
        if(!reader.row_layout) bad("conversion wrote wrong layout");
        reader.scan(0,std::numeric_limits<I64>::max(),{true,true,true,true,true,true},false,true,[](const Tick&){});
    }
    if(output_stats.selected!=expected || output_stats.blocks!=input_stats.blocks) bad("conversion changed count/block boundaries");
    std::cout << "{\"record_count\":" << expected << ",\"block_count\":" << output_stats.blocks << "}\n";
}
void import(const Options& o) {
    auto contract=contract_option(o); fs::path input=required(o,"--input"),output=required(o,"--output");
    if (!fs::is_directory(output) || !fs::is_empty(output)) throw Error(4,"import output must be an existing empty staging directory");
    std::ifstream source(input,std::ios::binary); if (!source) throw Error(4,"cannot open source");
    Writer writer(output,contract); std::string line; U64 row=0; I64 previous=-1, first=-1;
    for (;;) {
        auto line_number=add(row,1);
        try {
            if (!line_read(source,line)) break;
            row=line_number; auto t=parse(line,row);
            if (t.ns<previous) bad("timestamp regression");
            previous=t.ns; writer.append(t);
            if(first<0) first=t.ns;
        } catch (const Error& e) { throw Error(e.code,input.string()+":"+std::to_string(line_number)+": "+e.what()); }
    }
    if (!row) bad("empty source");
    writer.finish();
    // Verify all columns before handing staging back to the publishing wrapper.
    Stats stats;
    for (const auto& e:fs::directory_iterator(output)) if (e.path().extension()==".lmc") {
        Reader r(e.path(),contract,stats); r.scan(0,std::numeric_limits<I64>::max(),{true,true,true,true,true,true},false,true,[](const Tick&){});
    }
    std::cout << "{\"record_count\":" << row << ",\"first_timestamp_utc\":\"" << iso(first)
              << "\",\"last_timestamp_utc\":\"" << iso(previous) << "\"}\n";
}
void stats_output(const Options& o,const Stats& s,double elapsed,const std::string& contract,const std::string& kind) {
    if (!o.contains("--stats")) return;
    fs::path path=o.at("--stats"),temp=path.string()+".tmp";
    if (fs::exists(path) || fs::exists(temp)) throw Error(4,"stats output/temp exists");
    std::ofstream f(temp); if (!f) throw Error(4,"cannot write stats");
    try {
    f << std::setprecision(17) << "{\"version\":1,\"contract\":\"" << contract << "\",\"query\":\"" << kind
      << "\",\"from_utc\":\"" << iso(timestamp(o.at("--from"),false))
      << "\",\"to_utc\":\"" << iso(timestamp(o.at("--to"),false))
      << "\",\"scan_mode\":\"" << (o.contains("--scan-mode")?o.at("--scan-mode"):"pruned")
      << "\",\"files_examined\":" << s.files << ",\"blocks_examined\":" << s.blocks << ",\"blocks_skipped\":" << s.skipped
      << ",\"rows_decoded\":" << s.decoded << ",\"rows_selected\":" << s.selected << ",\"bytes_requested\":" << s.bytes
      << ",\"elapsed_seconds\":" << elapsed << "}\n";
    f.close(); if (!f) throw Error(4,"stats write failed");
    fs::rename(temp,path);
    } catch (...) { f.close(); std::error_code ec; fs::remove(temp,ec); throw; }
}
void query(const Options& o,const std::string& kind) {
    if (kind!="summary" && kind!="bars" && kind!="flow") throw Error(2,"unknown query");
    auto c=contract_option(o);
    I64 from,to;
    try { from=timestamp(required(o,"--from"),false); to=timestamp(required(o,"--to"),false); }
    catch(const Error& e) { throw Error(2,e.what()); }
    if (from>=to) throw Error(2,"--from must precede --to");
    I64 width=0;
    if (kind=="bars") {
        const std::map<std::string,I64> widths{{"1s",second},{"1m",60*second},{"5m",300*second},{"1h",3600*second},{"1d",day_ns}};
        auto b=required(o,"--bucket"); if (!widths.contains(b)) throw Error(2,"unsupported bucket"); width=widths.at(b);
    } else if (o.contains("--bucket")) throw Error(2,"--bucket requires bars");
    std::string mode=o.contains("--scan-mode")?o.at("--scan-mode"):"pruned";
    if (mode!="row" && mode!="column" && mode!="pruned") throw Error(2,"scan mode must be row, column or pruned");
    fs::path output,temp; std::ofstream result; std::ostream* out=&std::cout;
    if (o.contains("--output")) {
        output=o.at("--output"); temp=output.string()+".tmp";
        if (fs::exists(output) || fs::exists(temp)) throw Error(4,"query output/temp exists");
        result.open(temp); if (!result) throw Error(4,"cannot open output"); out=&result;
    }
    if (o.contains("--stats") && (fs::exists(o.at("--stats")) || fs::exists(o.at("--stats")+".tmp") ||
        (!output.empty() && (fs::path(o.at("--stats"))==output || fs::path(o.at("--stats"))==temp || fs::path(o.at("--stats")+".tmp")==output)))) {
        if (result.is_open()) { result.close(); fs::remove(temp); }
        throw Error(4,"stats output exists or conflicts with query output");
    }
    try {
        out->imbue(std::locale::classic()); *out << std::setprecision(17);
        Aggregate aggregate,bar; I64 bucket=-1; std::array<U64,3> counts{},volumes{}; Stats stats;
        auto start=std::chrono::steady_clock::now();
        auto emit_bar=[&] {
            if (!bar.count) return;
            *out << iso(bucket) << ',' << money(bar.open) << ',' << money(bar.high) << ',' << money(bar.low) << ',' << money(bar.close)
                 << ',' << bar.volume << ',' << bar.count << ',' << bar.vwap() << '\n';
        };
        if (kind=="bars") *out << "bucket_start_utc,open,high,low,close,volume_contracts,record_count,vwap\n";
        const std::array<bool,6> needed{false,true,true,kind=="flow",kind=="flow",true};
        U64 expected=0,actual=0; bool declared_row=false;
        auto inputs=partitions(fs::path(required(o,"--data"))/c,c,expected,declared_row);
        if(declared_row!=(mode=="row")) throw Error(2,"scan mode does not match dataset layout");
        for (const auto& p:inputs) {
            Reader reader(p,c,stats);
            if(reader.row_layout!=declared_row) bad("manifest/header layout mismatch");
            actual=add(actual,reader.record_count);
            reader.scan(from,to,needed,mode=="pruned",false,[&](const Tick& t) {
                if (kind=="summary") aggregate.append(t);
                else if (kind=="bars") {
                    I64 b=(t.ns/width)*width;
                    if (bucket!=b) { emit_bar(); bar={}; bucket=b; } bar.append(t);
                } else {
                    std::size_t side=2;
                    if (t.bid>0 && t.bid<t.ask) { if(t.last==t.ask) side=0; else if(t.last==t.bid) side=1; }
                    counts[side]=add(counts[side],1); volumes[side]=add(volumes[side],t.volume);
                }
            });
        }
        if(actual!=expected) bad("manifest record count differs from partitions");
        if (kind=="summary") {
            *out << "contract,record_count,volume_contracts,min_price,max_price,vwap\n" << c << ',' << aggregate.count << ',' << aggregate.volume << ',';
            if (aggregate.count) *out << money(aggregate.low) << ',' << money(aggregate.high) << ',' << aggregate.vwap(); else *out << ",,";
            *out << '\n';
        } else if (kind=="bars") emit_bar();
        else {
            U64 records=add(add(counts[0],counts[1]),counts[2]),volume=add(add(volumes[0],volumes[1]),volumes[2]);
            *out << "contract,estimated_buy_records,estimated_sell_records,unknown_records,estimated_buy_volume,estimated_sell_volume,unknown_volume,classified_record_fraction,classified_volume_fraction\n"
                 << c << ',' << counts[0] << ',' << counts[1] << ',' << counts[2] << ',' << volumes[0] << ',' << volumes[1] << ',' << volumes[2] << ',';
            if (records) *out << static_cast<double>(add(counts[0],counts[1]))/static_cast<double>(records);
            *out << ','; if (volume) *out << static_cast<double>(add(volumes[0],volumes[1]))/static_cast<double>(volume); *out << '\n';
        }
        out->flush(); if (!*out) throw Error(4,"query write failed");
        if (result.is_open()) { result.close(); if (!result) throw Error(4,"output close failed"); fs::rename(temp,output); }
        stats_output(o,stats,std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count(),c,kind);
    } catch (...) { if (result.is_open()) result.close(); if (!temp.empty()) { std::error_code ec; fs::remove(temp,ec); } throw; }
}
#ifndef LATTICE_NO_MAIN
int main(int argc,char** argv) {
    std::locale::global(std::locale::classic());
    try {
        if (argc==2 && std::string(argv[1])=="--version") { std::cout << "lattice-market 0.2.0\n"; return 0; }
        if (argc==1 || (argc==2 && std::string(argv[1])=="--help")) {
            std::cout << "lattice import --input FILE --contract ESZ26|NQZ26 --output EMPTY_STAGING\n"
                "lattice convert-row --data COLUMN_DATA --contract ID --output EMPTY_STAGING\n"
                "lattice query summary|bars|flow --data DIR --contract ID --from ISOZ --to ISOZ [--bucket 1s|1m|5m|1h|1d] [--scan-mode row|column|pruned] [--output FILE] [--stats FILE]\n"
                "lattice verify --data DIR --contract ID\nUse tools/import_dataset.py to publish imports. All ranges are [from,to), UTC.\n"; return 0;
        }
        std::string command=argv[1];
        if (command=="import") import(options(argc,argv,2,{"--input","--contract","--output"}));
        else if(command=="convert-row") convert_row(options(argc,argv,2,{"--data","--contract","--output"}));
        else if (command=="query" && argc>=3) query(options(argc,argv,3,{"--data","--contract","--from","--to","--bucket","--scan-mode","--output","--stats"}),argv[2]);
        else if (command=="verify") {
            auto o=options(argc,argv,2,{"--data","--contract"}); auto c=contract_option(o); Stats stats;
            U64 previous=0,expected=0; bool declared_row=false;
            for (const auto& p:partitions(fs::path(required(o,"--data"))/c,c,expected,declared_row)) {
                Reader r(p,c,stats);
                if(r.row_layout!=declared_row) bad("manifest/header layout mismatch");
                r.scan(0,std::numeric_limits<I64>::max(),{true,true,true,true,true,true},false,true,[&](const Tick& t) {
                    if(t.row<=previous) bad("source rows regress across partitions"); previous=t.row;
                });
            }
            if(stats.selected!=expected) bad("manifest record count differs from partitions");
            std::cout << "{\"verified_records\":" << stats.selected << "}\n";
        } else throw Error(2,"unknown command; use --help");
        return 0;
    } catch(const Error& e) { std::cerr << e.what() << '\n'; return e.code; }
      catch(const fs::filesystem_error& e) { std::cerr << e.what() << '\n'; return 4; }
      catch(const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
#endif

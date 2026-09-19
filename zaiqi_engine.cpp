#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cmath>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

using namespace std;
using Clock = chrono::steady_clock;

struct Move { int a,b; char reveal; int score; string uci() const { string s; s += char('a'+a%9); s += char('0'+a/9); s += char('a'+b%9); s += char('0'+b/9); if(reveal) s+=reveal; return s; } };
struct State { array<char,90> b{}; bool red=true; string bag; };
struct TT { int depth; int score; string best; };

static atomic<bool> stopFlag{false};
static mutex ioMutex;
static State pos;
static vector<string> banned;
static unordered_map<uint64_t,TT> table;
static int maxDepth=1, nodes=0;
static Clock::time_point deadline;

bool red(char p){ return p>='A'&&p<='Z'; }
bool hidden(char p){ return p=='X'||p=='x'; }
bool own(char p,bool r){ return p!='.' && (red(p)==r); }
char presumed(int sq){
  int r=sq/9,c=sq%9; string back="RNBAKABNR";
  if(r==0) return back[c]; if(r==2&&(c==1||c==7)) return 'C'; if(r==3&&c%2==0) return 'P';
  if(r==9) return tolower(back[c]); if(r==7&&(c==1||c==7)) return 'c'; if(r==6&&c%2==0) return 'p'; return 0;
}
char known(int sq,char p){ if(!hidden(p)) return p; char q=presumed(sq); return q?q:p; }
bool inb(int r,int c){ return r>=0&&r<10&&c>=0&&c<9; }

uint64_t key(const State&s){ uint64_t h=1469598103934665603ULL; for(char p:s.b){h^=(unsigned char)p;h*=1099511628211ULL;} h^=s.red; return h; }

bool attacked(const State&s,int sq,bool byRed){
  int tr=sq/9,tc=sq%9;
  for(int a=0;a<90;a++) if(own(s.b[a],byRed)) {
    char p=known(a,s.b[a]); int r=a/9,c=a%9,dr=tr-r,dc=tc-c;
    if(p=='P'||p=='p'){ int step=byRed?-1:1; if(dr==step&&abs(dc)<=1&&((byRed&&r<=4)||(!byRed&&r>=5))) return true; if(dr==step&&dc==0)return true; }
    else if(p=='N'||p=='n'){ if((abs(dr)==2&&abs(dc)==1)||(abs(dr)==1&&abs(dc)==2)){int br=r+(abs(dr)==2?dr/2:0),bc=c+(abs(dc)==2?dc/2:0); if(s.b[br*9+bc]=='.') return true;} }
    else if(p=='B'||p=='b'){ if(abs(dr)==2&&abs(dc)==2&&((byRed&&tr<=4)||(!byRed&&tr>=5))){int mr=(r+tr)/2,mc=(c+tc)/2;if(s.b[mr*9+mc]=='.')return true;} }
    else if(p=='A'||p=='a'){ if(abs(dr)==1&&abs(dc)==1&&((byRed&&tr>=7)||(!byRed&&tr<=2))) return true; }
    else if(p=='K'||p=='k'){ if((dr==0&&abs(dc)==1)||(dc==0&&abs(dr)==1)) return true; if(dc==0){int x=r+(dr>0?1:-1);bool clear=true;while(x!=tr){if(s.b[x*9+c]!='.')clear=false;x+=(dr>0?1:-1);}if(clear)return true;} }
    else if(p=='R'||p=='r'||p=='C'||p=='c'){ if(r==tr){int n=0;for(int x=min(c,tc)+1;x<max(c,tc);x++)if(s.b[r*9+x]!='.')n++; if((p=='R'||p=='r')?n==0:n==1)return true;} if(c==tc){int n=0;for(int x=min(r,tr)+1;x<max(r,tr);x++)if(s.b[x*9+c]!='.')n++; if((p=='R'||p=='r')?n==0:n==1)return true;} }
  } return false;
}
bool incheck(const State&s,bool r){ int k=-1;for(int i=0;i<90;i++)if(s.b[i]==(r?'K':'k'))k=i;return k>=0&&attacked(s,k,!r); }

void add(vector<Move>&v,const State&s,int a,int b){ if(!inb(b/9,b%9)||own(s.b[b],s.red))return; v.push_back({a,b,hidden(s.b[a])?presumed(a):0,0}); }
vector<Move> pseudo(const State&s){ vector<Move>v; for(int a=0;a<90;a++)if(own(s.b[a],s.red)){char p=known(a,s.b[a]);int r=a/9,c=a%9;auto step=[&](int dr,int dc){add(v,s,a,(r+dr)*9+c+dc);};
  if(p=='P'||p=='p'){step(s.red?-1:1,0);if((s.red&&r<=4)||(!s.red&&r>=5)){step(0,-1);step(0,1);}}
  else if(p=='N'||p=='n'){int ds[8][2]={{2,1},{2,-1},{-2,1},{-2,-1},{1,2},{1,-2},{-1,2},{-1,-2}};for(auto&d:ds){int br=r+(abs(d[0])==2?d[0]/2:0),bc=c+(abs(d[1])==2?d[1]/2:0);if(inb(br,bc)&&s.b[br*9+bc]=='.')step(d[0],d[1]);}}
  else if(p=='B'||p=='b'){for(int dr:{-2,2})for(int dc:{-2,2}){int nr=r+dr,nc=c+dc;if(inb(nr,nc)&&((s.red&&nr>=5)||(!s.red&&nr<=4))&&s.b[(r+dr/2)*9+c+dc/2]=='.')add(v,s,a,nr*9+nc);}}
  else if(p=='A'||p=='a'){for(int dr:{-1,1})for(int dc:{-1,1}){int nr=r+dr,nc=c+dc;if(inb(nr,nc)&&((s.red&&nr>=7)||(!s.red&&nr<=2)))add(v,s,a,nr*9+nc);}}
  else if(p=='K'||p=='k'){for(int dr:{-1,0,1})for(int dc:{-1,0,1})if(abs(dr)+abs(dc)==1){int nr=r+dr,nc=c+dc;if(inb(nr,nc)&&((s.red&&nr>=7)||(!s.red&&nr<=2)))add(v,s,a,nr*9+nc);}}
  else if(p=='R'||p=='r'||p=='C'||p=='c'){for(auto d:vector<pair<int,int>>{{1,0},{-1,0},{0,1},{0,-1}}){int nr=r+d.first,nc=c+d.second,screen=0;while(inb(nr,nc)){int q=nr*9+nc;if(s.b[q]=='.'){if(!screen)add(v,s,a,q);}else{if(p=='C'||p=='c'){screen++;if(screen==2)add(v,s,a,q);}break;}nr+=d.first;nc+=d.second;}}}
 } return v; }
vector<Move> legal(const State&s){vector<Move>out;for(auto m:pseudo(s)){State t=s;char p=t.b[m.a];t.b[m.b]=p;t.b[m.a]='.';t.red=!t.red;if(!incheck(t,!t.red))out.push_back(m);}return out;}

void apply_move(State&s,const Move&m){char p=s.b[m.a];s.b[m.b]=p;s.b[m.a]='.';if(m.reveal&&hidden(p))s.b[m.b]=s.red?m.reveal:tolower(m.reveal);s.red=!s.red;}
int val(char p){switch(toupper(p)){case 'K':return 10000;case 'R':return 900;case 'C':return 450;case 'N':return 400;case 'B':return 250;case 'A':return 250;case 'P':return 100;case 'X':return 180;}return 0;}
int eval(const State&s){int z=0;for(int i=0;i<90;i++){char p=known(i,s.b[i]);int x=val(p);int r=i/9,c=i%9;if(p=='P'||p=='p')x+=((red(p)&&r<5)||(!red(p)&&r>4))?35:0;z+=(red(p)?x:-x);}return s.red?z:-z;}
int search(State&s,int depth,int alpha,int beta,vector<Move>&pv){ ++nodes;if(depth<=0||stopFlag||Clock::now()>=deadline)return eval(s);uint64_t k=key(s);auto it=table.find(k);if(it!=table.end()&&it->second.depth>=depth)return it->second.score;auto ms=legal(s);if(ms.empty())return incheck(s,s.red)?-9000+depth:0;sort(ms.begin(),ms.end(),[](auto&a,auto&b){return a.score>b.score;});int best=-10000000;Move bm=ms[0];for(auto&m:ms){State t=s;apply_move(t,m);vector<Move>child;int q=-search(t,depth-1,-beta,-alpha,child);if(q>best){best=q;bm=m;pv.clear();pv.push_back(m);pv.insert(pv.end(),child.begin(),child.end());}alpha=max(alpha,q);if(alpha>=beta)break;}table[k]={depth,best,bm.uci()};return best;}

void parsefen(string f){pos=State{};pos.b.fill('.');stringstream ss(f);string board,side;ss>>board>>side>>pos.bag;int r=0,c=0;for(char ch:board){if(ch=='/'){r++;c=0;continue;}if(isdigit((unsigned char)ch))c+=ch-'0';else if(r<10&&c<9)pos.b[r*9+c++]=ch;}pos.red=side!="b";}
void applyuci(string u){if(u.size()<4)return;int a=(u[1]-'0')*9+u[0]-'a',b=(u[3]-'0')*9+u[2]-'a';if(a<0||a>=90||b<0||b>=90)return;Move m{a,b,(char)(u.size()>4?u[4]:0),0};apply_move(pos,m);}
void parseposition(const string&line){string x;size_t p=line.find("fen ");if(p!=string::npos){x=line.substr(p+4);size_t m=x.find(" moves ");if(m!=string::npos){string tail=x.substr(m+7);x=x.substr(0,m);parsefen(x);stringstream ss(tail);while(ss>>x)applyuci(x);}else parsefen(x);return;}pos=State{};pos.b.fill('.');string init="xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX";parsefen(init+" w A2B2N2R2C2P5a2b2n2r2c2p5");size_t m=line.find(" moves ");if(m!=string::npos){stringstream ss(line.substr(m+7));while(ss>>x)applyuci(x);}}
void go(){stopFlag=false;table.clear();deadline=Clock::now()+chrono::milliseconds(60000);vector<Move>best,pv;int score=0;for(int d=1;d<=30&&!stopFlag;d++){maxDepth=d;pv.clear();score=search(pos,d,-10000000,10000000,pv);if(stopFlag)break;best=pv;{lock_guard<mutex>g(ioMutex);cout<<"info depth "<<d<<" score cp "<<score<<" nodes "<<nodes<<" pv";for(auto&m:pv)cout<<" "<<m.uci();cout<<"\n";cout.flush();}if(Clock::now()>=deadline)break;}string out=best.empty()?"0000":best[0].uci();for(auto&b:banned)if(out==b){out="0000";break;}lock_guard<mutex>g(ioMutex);cout<<"bestmove "<<out<<"\n";cout.flush();}
int main(){ios::sync_with_stdio(false);cin.tie(nullptr);string line;thread worker;while(getline(cin,line)){if(line=="uci"){cout<<"id name ZaiQi 1.0\n id author zai\noption name Threads type spin default 1 min 1 max 64\noption name Hash type spin default 256 min 1 max 4096\noption name Ponder type check default true\noption name MultiPV type spin default 1 min 1 max 20\noption name EvalFile type string default\nuciok\n"<<flush;}else if(line=="isready")cout<<"readyok\n"<<flush;else if(line.rfind("position ",0)==0)parseposition(line);else if(line.rfind("banmoves ",0)==0){banned.clear();stringstream s(line.substr(9));string m;while(s>>m)banned.push_back(m.substr(0,4));}else if(line=="stop"||line=="ponderhit"){stopFlag=true;if(worker.joinable())worker.join();}else if(line.rfind("go ",0)==0){if(worker.joinable())worker.join();worker=thread(go);}else if(line=="ucinewgame"){table.clear();banned.clear();}else if(line=="quit"){stopFlag=true;if(worker.joinable())worker.join();break;}else if(line=="d"){lock_guard<mutex>g(ioMutex);cout<<"info string ZaiQi position side "<<(pos.red?'w':'b')<<"\n"<<flush;}}return 0;}

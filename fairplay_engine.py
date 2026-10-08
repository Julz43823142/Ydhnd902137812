"""Small fixed-node UCI transport for Fair Play's independent Stockfish pool.

Only the first PV move and score are consumed by Fair Play. python-chess remains
the chess/score authority, but constructing every intermediate principal
variation and allocating an asyncio loop thread per engine is unnecessary here.
No global monkeypatch; normal chess and Game Review keep their existing engine.
"""
import os
import re
import select
import subprocess
import time
from types import SimpleNamespace

import chess
import chess.engine


class CoherentCandidates:
    """Last complete exact MultiPV round, never a mixture of search depths."""
    def __init__(self,count):
        self.count=count;self.depths={};self.complete=None;self.depth=-1
    def add(self,index,row):
        depth=row.get('depth')
        if (depth is None or 'pv' not in row or 'score' not in row
                or row.get('lowerbound') or row.get('upperbound')):return
        if index==1:self.depths[depth]={}
        group=self.depths.setdefault(depth,{})
        group[index]=dict(row)
        if index==self.count and all(i in group for i in range(1,self.count+1)):
            rows=[group[i] for i in range(1,self.count+1)]
            scores=[r['score'].relative.score(mate_score=10000) for r in rows]
            if (depth>=self.depth and len({r['pv'][0] for r in rows})==self.count
                    and all(a>=b for a,b in zip(scores,scores[1:]))):
                self.complete=rows;self.depth=depth
        for old in sorted(self.depths)[:-4]:self.depths.pop(old,None)
    def result(self):
        if self.complete is None:
            raise chess.engine.EngineError('No complete exact MultiPV iteration')
        return self.complete


class CompactNodeEngine:
    """Synchronous, bounded POSIX transport; owned by exactly one pool worker."""
    def __init__(self, command, timeout=15.0):
        if os.name != 'posix':
            raise OSError('Compact Stockfish transport requires POSIX pipes')
        self.timeout = timeout
        self.id = {}
        self.options = {}
        self._buffer = b''
        self._first = True
        self._settings = {}
        self._closed = False
        self.process = subprocess.Popen(command if isinstance(command, list) else [command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, close_fds=True)
        self.transport = SimpleNamespace(get_pid=lambda: self.process.pid)
        try:
            self._send('uci')
            deadline = time.monotonic()+timeout
            while True:
                line = self._read(deadline)
                if line == 'uciok':break
                if line.startswith('id '):
                    _, key, value = line.split(' ', 2)
                    self.id[key] = value
                elif line.startswith('option name '):
                    self._option(line)
            if 'MultiPV' not in self.options:
                raise chess.engine.EngineError('Stockfish MultiPV is unavailable')
        except BaseException:
            self.close()
            raise

    @classmethod
    def popen_uci(cls, command, *, timeout=15.0, **kwargs):
        return cls(command, timeout)

    def _option(self, line):
        match = re.match(r'option name (.+?) type (\w+)(.*)', line)
        if not match:return
        name, kind, rest = match.groups()
        def integer(field):
            found = re.search(r'\b'+field+r' (-?\d+)', rest)
            return int(found[1]) if found else None
        self.options[name] = SimpleNamespace(min=integer('min'), max=integer('max'), type=kind)

    def _send(self, line):
        if self._closed or self.process.poll() is not None:
            raise chess.engine.EngineTerminatedError('Stockfish process ended')
        try:
            self.process.stdin.write((line+'\n').encode('ascii'))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise chess.engine.EngineTerminatedError('Stockfish pipe closed') from error

    def _read(self, deadline):
        while b'\n' not in self._buffer:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Stockfish response deadline exceeded')
            readable,_,_=select.select([self.process.stdout],[],[],remaining)
            if not readable:raise TimeoutError('Stockfish response deadline exceeded')
            chunk=os.read(self.process.stdout.fileno(),65536)
            if not chunk:raise chess.engine.EngineTerminatedError('Stockfish output ended')
            self._buffer+=chunk
            if len(self._buffer)>1024*1024:
                raise chess.engine.EngineError('Stockfish output limit exceeded')
        line,self._buffer=self._buffer.split(b'\n',1)
        return line.decode('ascii',errors='replace').strip()

    def configure(self, options):
        for name,value in options.items():
            if name not in self.options:raise chess.engine.EngineError('Unsupported engine option')
            if self.options[name].type != 'button' and self._settings.get(name,object()) == value:continue
            rendered = str(value).lower() if isinstance(value,bool) else str(value)
            self._send('setoption name '+name+('' if value is None else ' value '+rendered))
            self._settings[name]=value

    def analyse(self, board, limit, *, multipv=None, root_moves=None):
        if board.chess960 or board.move_stack or not limit.nodes or limit.nodes<=0:
            raise chess.engine.EngineError('Compact analysis requires an independent standard fixed-node position')
        options={'MultiPV':multipv or 1}
        for key,value in [('Ponder',False),('UCI_AnalyseMode',True),('UCI_Chess960',False)]:
            if key in self.options:options[key]=value
        self.configure(options)
        deadline=time.monotonic()+self.timeout
        if self._first:
            self._send('ucinewgame')
            self._send('isready')
            while self._read(deadline)!='readyok':pass
            self._first=False
        self._send('position fen '+board.fen(en_passant='fen'))
        command='go nodes '+str(int(limit.nodes))
        if root_moves is not None:
            moves=list(root_moves)
            if not moves or any(move not in board.legal_moves for move in moves):
                raise chess.engine.EngineError('Invalid root move restriction')
            command+=' searchmoves '+' '.join(move.uci() for move in moves)
        self._send(command)
        rows={}
        coherent=CoherentCandidates(min(multipv or 1,len(moves) if root_moves is not None else board.legal_moves.count()))
        while True:
            line=self._read(deadline)
            if line.startswith('bestmove '):break
            parsed=self.parse_info(line,board.turn)
            if parsed is not None:
                index,values=parsed
                rows.setdefault(index,{}).update(values)
                if coherent is not None:coherent.add(index,values)
        values=coherent.result() if coherent is not None else [rows[i] for i in sorted(rows) if 'score' in rows[i] and 'pv' in rows[i]]
        if not values:raise chess.engine.EngineError('Stockfish returned no scored candidates')
        return values if multipv is not None else values[0]

    @staticmethod
    def parse_info(line, turn):
        if not line.startswith('info ') or line.startswith('info string '):return None
        # Score and first PV move are all downstream metrics use. Bounds are
        # retained so incomplete or bounded rounds cannot become exact evidence.
        tokens=line.split()
        def value(key):
            try:return tokens[tokens.index(key)+1]
            except (ValueError,IndexError):return None
        try:
            index=int(value('multipv') or 1)
            row={}
            if 'score' in tokens:
                at=tokens.index('score')
                kind,amount=tokens[at+1],int(tokens[at+2])
                if kind not in ('cp','mate'):return None
                score=chess.engine.Cp(amount) if kind=='cp' else chess.engine.Mate(amount)
                row['score']=chess.engine.PovScore(score,turn)
            move=value('pv')
            if move:row['pv']=[chess.Move.from_uci(move)]
            if not row:return None
            if value('depth') is not None:row['depth']=int(value('depth'))
            if 'lowerbound' in tokens:row['lowerbound']=True
            if 'upperbound' in tokens:row['upperbound']=True
            return (index,row)
        except (ValueError,IndexError):return None

    def quit(self):
        self.close()

    def close(self):
        if self._closed:return
        try:
            if self.process.poll() is None:
                try:self._send('quit')
                except (OSError,chess.engine.EngineError):pass
                try:self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=2)
        finally:
            self._closed=True
            for stream in (self.process.stdin,self.process.stdout):
                if stream is not None:stream.close()

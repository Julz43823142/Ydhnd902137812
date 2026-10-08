"""Isolated local-only CPU policy worker. stdout is protocol, never logs."""
import hashlib
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fairplay_maia import MODEL_SHA256, MAX_POSITIONS


def main():
    path=Path(sys.argv[1])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=MODEL_SHA256:
        raise ValueError('Checkpoint verification failed')
    import torch
    import chess
    from maia3.uci import parse_args
    from maia3.models import MAIA3Model
    from maia3.dataset import tokenize_board, get_historical_tokens, get_legal_moves_mask
    from maia3.utils import get_all_possible_moves, mirror_move
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    cfg=parse_args(['--model','maia3-5m','--checkpoint-path',str(path),'--device','cpu','--no-use-amp','--local-files-only'])
    model=MAIA3Model(cfg)
    state=torch.load(path,map_location='cpu',weights_only=True)
    state=state.get('model_state_dict',state)
    model.load_state_dict({key.replace('smolgen','gab'):value for key,value in state.items()},strict=True)
    model.eval()
    moves=get_all_possible_moves();mapping={move:i for i,move in enumerate(moves)}
    print(json.dumps({'ready':MODEL_SHA256}),flush=True)
    for line in sys.stdin:
        if len(line)>2*1024*1024:raise ValueError('Request too large')
        request=json.loads(line);positions=request['positions']
        if not 0<len(positions)<=MAX_POSITIONS:raise ValueError('Invalid batch size')
        results=[]
        with torch.inference_mode():
            for offset in range(0,len(positions),32):
                batch=positions[offset:offset+32]
                boards=[];inputs=[];masks=[]
                for item in batch:
                    if not 1<=len(item['history'])<=8:raise ValueError('Invalid board history')
                    history=[chess.Board(fen) for fen in item['history']]
                    board=history[-1];boards.append(board)
                    inputs.append(get_historical_tokens([tokenize_board(b) for b in history],cfg,0,0,0,0))
                    masks.append(get_legal_moves_mask(board,mapping))
                logits,_,_=model(torch.stack(inputs),
                    torch.tensor([x['rating'] for x in batch],dtype=torch.long),
                    torch.tensor([x['opponent_rating'] for x in batch],dtype=torch.long))
                probabilities=torch.softmax(logits.float().masked_fill(~torch.stack(masks),float('-inf')),dim=-1)
                for board,row in zip(boards,probabilities.tolist()):
                    policy={}
                    for move in board.legal_moves:
                        encoded=move.uci() if board.turn else mirror_move(move.uci())
                        policy[move.uci()]=row[mapping[encoded]]
                    results.append(policy)
        print(json.dumps({'policies':results},separators=(',',':')),flush=True)


if __name__=='__main__':
    try:main()
    except Exception:
        # Never print inputs, positions, checkpoint paths or exception payloads.
        raise SystemExit(1)

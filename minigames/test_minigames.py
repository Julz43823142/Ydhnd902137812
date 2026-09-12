"""Offline rule and UI tests. No Discord messages or real wallet writes."""
import copy
import io
import random
import unittest
from collections import Counter
import minigames_engine as e
import minigames_poker as p
import minigames_blackjack as bj
from minigames_content import select_ladder


def setup(kind,stake=0,n=2):
    s={'games':{},'seen':{},'daily':{}}
    w={str(i):{'name':f'Player {i}','coins':100.,'points':123.5,'active_badge':''} for i in range(n)}
    e.create(s,w,'g','0','Player 0',kind,stake,1000)
    for i in range(1,n):e.action(s,w,'g',str(i),f'Player {i}','join','',1001,random.Random(5),[])
    return s,w


def bank():
    return [{'id':f'{diff}{i}','question':f'{diff} question {i}','options':['a','b','c','d'], 'answer':'a','category':str(i%5),'difficulty':diff} for diff in ['easy','medium','hard'] for i in range(20)]


def start(s,w):
    e.action(s,w,'g','0','Player 0','start','',1002,random.Random(42),bank())
    return s['games']['g']


def act(s,w,uid,command,value='',now=1003):
    return e.action(s,w,'g',str(uid),'Player '+str(uid),command,value,now,random.Random(71),bank())


class Rules(unittest.TestCase):
    def test_connect_free_win_points_unchanged(self):
        s,w=setup('connect');g=start(s,w)
        for i,col in enumerate([0,1,0,1,0,1,0]):act(s,w,i%2,'drop',col)
        self.assertEqual(g['winners'],['0']);self.assertEqual(w['0']['coins'],105)
        self.assertEqual(w['0']['points'],123.5)
        e.finish(s,w,g,['0'],1010,'again');self.assertEqual(w['0']['coins'],105)
    def test_connect_wager_atomic_and_draw(self):
        s,w=setup('connect',10);g=start(s,w)
        self.assertEqual([x['coins'] for x in w.values()],[90,90])
        e.finish(s,w,g,[],1005,'Draw')
        self.assertEqual([x['coins'] for x in w.values()],[100,100])
    def test_stake_win_pot_no_bonus(self):
        s,w=setup('rps',15);start(s,w);act(s,w,0,'choose','rock');act(s,w,1,'choose','scissors')
        self.assertEqual([x['coins'] for x in w.values()],[115,85])
        self.assertEqual([x['points'] for x in w.values()],[123.5,123.5])
    def test_wrong_player_stale_input_and_duplicate_choice(self):
        s,w=setup('connect');g=start(s,w)
        with self.assertRaises(ValueError):act(s,w,1,'drop',0)
        with self.assertRaises(ValueError):e.action(s,w,'g','0','P','drop',0,1003,random.Random(),[],expected=-1)
        s,w=setup('rps');start(s,w);act(s,w,0,'choose','paper')
        with self.assertRaises(ValueError):act(s,w,0,'choose','rock')
    def test_unlimited_rewards_and_forfeit_do_not_mint(self):
        s,w=setup('connect');g=start(s,w);act(s,w,0,'leave')
        self.assertEqual(w['1']['coins'],100)
        self.assertEqual(e.reward(s,w,g,'1',100,1004),100)
        self.assertEqual(e.reward(s,w,g,'1',1,1005),1)
    def test_ship_fleet_valid_and_private_setup(self):
        for seed in range(100):
            fleet=e.fleet(random.Random(seed));flat=sum(fleet,[])
            self.assertEqual([len(x) for x in fleet],[5,4,3,3,2]);self.assertEqual(len(set(flat)),17)
            self.assertTrue(all(0<=c<100 for c in flat))
        s,w=setup('ships');g=start(s,w)
        act(s,w,0,'ready');act(s,w,1,'ready')
        with self.assertRaises(ValueError):act(s,w,0,'randomize')
        with self.assertRaises(ValueError):act(s,w,1,'fire','A1')
        act(s,w,0,'fire','A1');self.assertEqual(g['data']['turn'],1)
    def test_mines_first_click_safe_and_solvable(self):
        self.assertEqual((e.MINEFIELD_WIDTH,e.MINEFIELD_HEIGHT,e.MINEFIELD_CELLS,e.MINEFIELD_MINES),(5,16,80,19))
        for seed in range(100):
            first=seed*7%e.MINEFIELD_CELLS;mines=set(e.mine_layout(first,random.Random(seed)))
            self.assertEqual(len(mines),e.MINEFIELD_MINES)
            self.assertFalse(mines&({first}|e.neighbors(first)));self.assertTrue(e.solvable(mines,first))
        s,w=setup('mines',0,1);g=start(s,w);act(s,w,0,'reveal','A1')
        mine=g['data']['mines'][0];r,c=divmod(mine,e.MINEFIELD_WIDTH);act(s,w,0,'reveal',chr(65+c)+str(r+1))
        self.assertEqual(w['0']['coins'],100);self.assertEqual(g['status'],'finished')
    def test_mines_free_win(self):
        s,w=setup('mines',0,1);g=start(s,w);act(s,w,0,'reveal','A1')
        for cell in range(e.MINEFIELD_CELLS):
            if g['status']=='finished':break
            if cell not in g['data']['mines'] and cell not in g['data']['open']:
                r,c=divmod(cell,e.MINEFIELD_WIDTH);act(s,w,0,'reveal',chr(65+c)+str(r+1))
        self.assertEqual(w['0']['coins'],105)
    def test_mines_click_grid_mode_and_stale_clicks(self):
        s,w=setup('mines',0,1);g=start(s,w)
        self.assertEqual(g['data']['mode'],'reveal');self.assertEqual(g['data']['modes']['0'],'reveal')
        self.assertEqual((g['data']['width'],g['data']['height'],g['data']['count']),(5,16,19))
        rev=g['rev'];e.action(s,w,'g','0','Player 0','mine_click','0',1003,random.Random(71),bank(),rev)
        self.assertIn(0,g['data']['open']);self.assertNotIn(0,g['data']['mines'])
        rev=g['rev'];e.action(s,w,'g','0','Player 0','mine_mode','',1004,random.Random(71),bank(),rev)
        self.assertEqual(g['data']['mode'],'flag');self.assertEqual(g['data']['modes']['0'],'flag')
        target=next(cell for cell in range(e.MINEFIELD_CELLS) if cell not in g['data']['open'])
        stale=rev  # Deliberately one revision old: fast grid clicks must still be safe.
        e.action(s,w,'g','0','Player 0','mine_click',str(target),1005,random.Random(71),bank(),stale)
        self.assertIn(target,g['data']['flags'])
        current=g['rev'];e.action(s,w,'g','0','Player 0','mine_click',str(target),1006,random.Random(71),bank(),current)
        self.assertNotIn(target,g['data']['flags'])
    def test_mines_multiplayer_lobby_supports_one_to_eight_players(self):
        s,w=setup('mines',0,8)
        self.assertEqual(len(s['games']['g']['players']),8)
        w['8']={'name':'Player 8','coins':100.,'points':123.5,'active_badge':''}
        with self.assertRaisesRegex(ValueError,'lobby is full'):
            e.action(s,w,'g','8','Player 8','join','',1001,random.Random(5),[])
        start(s,w)
        self.assertEqual(s['games']['g']['status'],'playing')

    def test_mines_multiplayer_shared_board_personal_modes_and_team_reward(self):
        self.assertEqual(e.LIMITS['mines'],8)
        s,w=setup('mines',0,3);g=start(s,w)
        self.assertEqual(len(g['players']),3)
        self.assertEqual(g['data']['modes'],{'0':'reveal','1':'reveal','2':'reveal'})

        # Player 1 switches to Flag, but Player 0 remains in Reveal.
        act(s,w,1,'mine_mode','flag')
        self.assertEqual(g['data']['modes']['1'],'flag')
        self.assertEqual(g['data']['modes']['0'],'reveal')
        act(s,w,1,'mine_mode','flag')  # explicit mode is idempotent, not a toggle
        self.assertEqual(g['data']['modes']['1'],'flag')

        # Player 0's first click still reveals and generates a fair shared board.
        rev=g['rev']
        e.action(s,w,'g','0','Player 0','mine_click','0',1004,random.Random(71),bank(),rev)
        self.assertIn(0,g['data']['open'])
        self.assertNotIn(0,g['data']['mines'])

        # Player 1's same-style board click uses only their personal Flag mode.
        target=next(cell for cell in range(e.MINEFIELD_CELLS) if cell not in g['data']['open'])
        e.action(s,w,'g','1','Player 1','mine_click',str(target),1005,random.Random(72),bank(),g['rev'])
        self.assertIn(target,g['data']['flags'])
        self.assertNotIn(target,g['data']['open'])

        # Player 2 can leave without killing the team run.
        act(s,w,2,'leave',now=1006)
        self.assertEqual(g['status'],'playing')
        self.assertIn('2',g['left'])

        # Clear every remaining safe cell with Player 0. Contributors 0 and 1
        # each earn +5; the player who left earns nothing.
        if target in g['data']['flags']:
            act(s,w,1,'mine_click',str(target),now=1007)  # unflag in personal Flag mode
        now=1008
        for cell in range(e.MINEFIELD_CELLS):
            if g['status']=='finished':break
            if cell not in g['data']['mines'] and cell not in g['data']['open']:
                e.action(s,w,'g','0','Player 0','reveal',chr(65+cell%e.MINEFIELD_WIDTH)+str(cell//e.MINEFIELD_WIDTH+1),
                         now,random.Random(73),bank(),g['rev'])
                now+=1
        self.assertEqual(g['status'],'finished')
        self.assertEqual(set(g['winners']),{'0','1'})
        self.assertEqual(w['0']['coins'],105)
        self.assertEqual(w['1']['coins'],105)
        self.assertEqual(w['2']['coins'],100)

    def test_mines_multiplayer_mine_hit_ends_shared_run(self):
        s,w=setup('mines',0,2);g=start(s,w)
        act(s,w,0,'reveal','A1')
        mine=g['data']['mines'][0]
        r,c=divmod(mine,e.MINEFIELD_WIDTH)
        act(s,w,1,'reveal',chr(65+c)+str(r+1),now=1004)
        self.assertEqual(g['status'],'finished')
        self.assertEqual(g['winners'],[])
        self.assertEqual([w[str(i)]['coins'] for i in range(2)],[100,100])
        self.assertIn('Player 1 hit a mine',g['result'])

    def test_mines_multiplayer_simultaneous_first_click_cannot_instantly_blow_team_up(self):
        s,w=setup('mines',0,2);g=start(s,w)
        original_rev=g['rev']
        e.action(s,w,'g','0','Player 0','mine_click','0',1003,random.Random(81),bank(),original_rev)
        self.assertTrue(g['data']['mines'])
        self.assertEqual(g['data']['layout_rev'],original_rev+1)

        second=next(cell for cell in range(e.MINEFIELD_CELLS) if cell not in g['data']['open'])
        with self.assertRaisesRegex(ValueError,'teammate just opened the first square'):
            e.action(s,w,'g','1','Player 1','mine_click',str(second),1003.1,random.Random(82),bank(),original_rev)
        self.assertEqual(g['status'],'playing')
        self.assertEqual(w['0']['coins'],100);self.assertEqual(w['1']['coins'],100)

    def test_mines_legacy_board_migrates_to_5x16_hard(self):
        s,w=setup('mines',0,1);g=start(s,w)
        g['data']={'mines':[1,2,3],'open':[0],'flags':[],'size':8,'count':10,'mode':'flag','quadrant':2,'phase_rev':g['rev']}
        before=g['rev'];e.migrate_minefield_button_board(s,w,1010)
        self.assertEqual((g['data']['width'],g['data']['height'],g['data']['count']),(5,16,19))
        self.assertEqual(g['data']['mines'],[]);self.assertEqual(g['data']['open'],[]);self.assertEqual(g['data']['mode'],'reveal')
        self.assertEqual(g['data']['modes'],{'0':'reveal'});self.assertEqual(g['data']['contributors'],[])
        self.assertIsNone(g['data']['layout_rev'])
        self.assertGreater(g['rev'],before)
    def test_trivia_fifteen_correct_ten_coins_and_seen(self):
        s,w=setup('trivia',0,1);g=start(s,w);now=1004
        for j in range(15):
            q=g['data']['questions'][j];act(s,w,0,'answer',q['options'].index(q['answer']),now)
            if j<14:now+=8;e.tick(s,w,'g',now,random.Random(1));now+=1
        self.assertEqual(w['0']['coins'],110);self.assertEqual(w['0']['points'],123.5)
        self.assertEqual(len(set(s['seen']['0'])),15)
    def test_trivia_avoids_recent_and_mixed_categories(self):
        first=select_ladder(bank(),[],random.Random(1));second=select_ladder(bank(),[q['id'] for q in first],random.Random(2))
        self.assertFalse({q['id'] for q in first}&{q['id'] for q in second})
    def test_escape_all_chapters_and_clues(self):
        s,w=setup('escape');g=start(s,w);now=1003
        act(s,w,1,'inspect',now=now)
        for room in g['data']['rooms']:
            now+=6;act(s,w,0,'solve',room['answer'],now)
        self.assertEqual(g['status'],'finished');self.assertEqual(w['0']['coins'],110);self.assertEqual(w['1']['coins'],110)

    def test_escape_solo_can_start_and_receives_every_clue(self):
        s,w=setup('escape',n=1);g=start(s,w)
        self.assertEqual(g['status'],'playing')
        room=g['data']['rooms'][0]
        packet=e.escape_clue_text(g,'0')
        self.assertIn('Solo mode',packet)
        for clue in room.get('clues',[]):
            self.assertIn(str(clue),packet)
        now=1003
        for room in g['data']['rooms']:
            now+=6;act(s,w,0,'solve',room['answer'],now)
        self.assertEqual(g['status'],'finished')
        self.assertEqual(w['0']['coins'],110)

    def test_escape_last_remaining_player_gets_all_clues(self):
        s,w=setup('escape',n=2);g=start(s,w)
        g.setdefault('left',[]).append('1')
        packet=e.escape_clue_text(g,'0')
        self.assertIn('Solo mode',packet)
        for clue in g['data']['rooms'][0].get('clues',[]):
            self.assertIn(str(clue),packet)
    def test_escape_is_procedurally_diverse(self):
        from minigames_escape import build_escape, PUZZLE_TEMPLATES, ESCAPE_BASE_MECHANIC_COUNT
        signatures=set();templates=set();themes=set();openers=set();structures=set()
        for seed in range(2500):
            adventure=build_escape(None,random.Random(seed))
            self.assertEqual(len(adventure['rooms']),8)
            template_line=tuple(room['template_id'] for room in adventure['rooms'])
            self.assertEqual(len(set(template_line)),8)
            self.assertNotIn('symbol_code', ' '.join(room['family'] for room in adventure['rooms']))
            signatures.add(template_line);templates.update(template_line);themes.add(adventure['theme'])
            openers.add(adventure['rooms'][0]['template_id']);structures.update(r['structure'] for r in adventure['rooms'])
        # v5 must be a genuine one-bank random sampler: 500+ concrete recipes,
        # every recipe is allowed in every room position, and no fixed first/second pool.
        self.assertGreaterEqual(len(PUZZLE_TEMPLATES),500)
        self.assertGreaterEqual(ESCAPE_BASE_MECHANIC_COUNT,120)
        self.assertEqual(len(PUZZLE_TEMPLATES),520)
        self.assertEqual(len(templates),520)
        self.assertGreaterEqual(len(openers),500)
        self.assertGreaterEqual(len(themes),95)
        self.assertGreaterEqual(len(signatures),2490)
        self.assertEqual(structures,{'single','transform','dual','triple'})

    def test_escape_v5_retires_persisted_legacy_run(self):
        s,w=setup('escape');g=start(s,w)
        self.assertEqual(g['data'].get('escape_build'),e.ESCAPE_BUILD)
        # Simulate an encrypted persisted adventure created by an older build.
        g['data']['escape_build']='escape-v2-legacy'
        g['data']['generation']='procedural-v2'
        changed=e.retire_legacy_escape_games(s,w,1010)
        self.assertEqual(changed,1)
        self.assertEqual(g['status'],'finished')
        self.assertIn('upgraded',g['result'].lower())

    def test_escape_random_order_and_answers_are_flexible(self):
        from minigames_escape import build_escape,answer_matches
        adventure=build_escape(None,random.Random(91))
        self.assertEqual([r['difficulty'] for r in adventure['rooms']],
                         ['Warm-up','Easy','Developing','Medium','Challenging','Hard','Expert','Master'])
        self.assertEqual(adventure['variety']['themes'],100)
        self.assertGreaterEqual(adventure['variety']['base_mechanics'],120)
        self.assertGreaterEqual(adventure['variety']['templates'],500)
        self.assertEqual(adventure['variety']['fixed_stage_pools'],0)
        self.assertEqual(len({r['template_id'] for r in adventure['rooms']}),8)
        for room in adventure['rooms']:
            self.assertTrue(answer_matches(room,room['answer']))
            decorated='  '+str(room['answer']).lower()+'  '
            self.assertTrue(answer_matches(room,decorated))

    def test_escape_numeric_master_lock_uses_real_numeric_values(self):
        from minigames_escape import _answer_score, _triple_result
        self.assertEqual(_answer_score('0'),0)
        self.assertEqual(_answer_score('120'),120)
        self.assertEqual(_answer_score('20'),20)
        answer,_=_triple_result(7,['0','120','20'])
        self.assertEqual(answer,'120')

    def test_lingo_feedback_and_one_coin_reward(self):
        # Duplicate letters are consumed exactly once, like Lingo/Wordle feedback.
        self.assertEqual(e.lingo_feedback('APPLE','ALLEY'),'GYXYX')
        s,w=setup('lingo',0,1);g=start(s,w)
        target=g['data']['target']
        act(s,w,0,'lingo_guess',target)
        self.assertEqual(g['status'],'finished')
        self.assertEqual(w['0']['coins'],101)

    def test_bomb_two_player_roles_and_team_reward(self):
        s,w=setup('bomb',0,2);g=start(s,w);now=1005
        for module_index in range(3):
            d=g['data'];active=[p['id'] for p in g['players'] if p['id'] not in g.get('left',[])]
            operator=active[d['module']%len(active)]
            helper=next(uid for uid in active if uid!=operator)
            self.assertIn('MANUAL',e.bomb_clue_text(g,helper).upper())
            answer=d['modules'][d['module']]['answer']
            act(s,w,int(operator),'bomb_code',answer,now);now+=4
        self.assertEqual(g['status'],'finished')
        self.assertEqual(w['0']['coins'],105);self.assertEqual(w['1']['coins'],105)

    def test_math_rush_run_reward_is_one_coin(self):
        s,w=setup('math',0,1);g=start(s,w)
        answer=g['data']['current']['a']
        act(s,w,0,'math_answer',answer,1003)
        self.assertEqual(g['data']['score'],1)
        e.tick(s,w,'g',g['data']['ends_at']+1,random.Random(3))
        self.assertEqual(g['status'],'finished')
        self.assertEqual(w['0']['coins'],101)

    def test_detective_case_can_be_solved(self):
        s,w=setup('detective',0,1);g=start(s,w)
        self.assertIn('Evidence',e.detective_case_text(g))
        culprit=g['data']['answer']
        act(s,w,0,'accuse',culprit)
        self.assertEqual(g['status'],'finished')
        self.assertEqual(w['0']['coins'],103)

    def test_math_wrong_answer_keeps_same_problem(self):
        s,w=setup('math',0,1);g=start(s,w)
        before=dict(g['data']['current'])
        wrong=str(int(before['a'])+1)
        act(s,w,0,'math_answer',wrong,1003)
        self.assertEqual(g['data']['current'],before)
        self.assertIn('same equation',g['data']['notice'].lower())
        self.assertNotIn(str(before['a']),g['data']['notice'])
        self.assertEqual(g['data']['score'],0)
        self.assertEqual(g['data']['misses'],1)

    def test_detective_first_name_only_when_unambiguous(self):
        data={'answer':'Harper Lane','suspects':[{'name':'Harper Lane'},{'name':'Harper Vale'},{'name':'Casey Knight'}]}
        self.assertFalse(e.detective_answer_matches(data,'Harper'))
        self.assertTrue(e.detective_answer_matches(data,'Harper Lane'))
        data={'answer':'Casey Knight','suspects':[{'name':'Harper Lane'},{'name':'Harper Vale'},{'name':'Casey Knight'}]}
        self.assertTrue(e.detective_answer_matches(data,'Casey'))

    def test_detective_needs_combined_clues_and_is_unique(self):
        for seed in range(200):
            d=e.detective_new(random.Random(seed))
            culprit=next(i for i,sus in enumerate(d['suspects']) if sus['name']==d['answer'])
            possible=set(range(len(d['suspects'])))
            self.assertEqual(len(d['suspects']),8)
            self.assertEqual(len(d['evidence']),6)
            self.assertGreaterEqual(len({clue['family'] for clue in d['evidence']}),4)
            for clue in d['evidence']:
                self.assertGreaterEqual(len(clue['matches']),3)
                self.assertIn(culprit,clue['matches'])
                possible &= set(clue['matches'])
            self.assertEqual(possible,{culprit})

    def test_codebreaker_transcript_is_unique_and_duplicate_safe(self):
        self.assertEqual(e.codebreaker_score((1,1,2),(1,2,1)),(1,2))
        from itertools import product
        for mode,length,seeds in [('easy',3,12),('normal',4,6),('hard',5,3)]:
            for seed in range(seeds):
                d=e.codebreaker_new(random.Random(seed),mode)
                self.assertEqual(d['length'],length);self.assertEqual(d['mode'],mode)
                possibles=[]
                for candidate in product(range(10),repeat=length):
                    if all(e.codebreaker_score(candidate,tuple(map(int,row['guess'])))==(row['exact'],row['wrong']) for row in d['clues']):
                        possibles.append(candidate)
                        if len(possibles)>1:break
                self.assertEqual(len(possibles),1)
                self.assertEqual(''.join(map(str,possibles[0])),d['answer'])
        s={'games':{},'seen':{},'daily':{}};w={'0':{'name':'Player 0','coins':100.,'points':0,'active_badge':''}}
        e.create(s,w,'g','0','Player 0','codebreaker',0,1000,mode='hard')
        g=start(s,w);self.assertEqual(g['data']['length'],5)
        act(s,w,0,'codebreaker_submit',g['data']['answer'])
        self.assertEqual(g['status'],'finished');self.assertEqual(w['0']['coins'],102)

    def test_blackjack_split_all_ten_value_cards(self):
        for a,b in [('Jc','Kh'),('Td','Qs'),('Kc','Jh'),('Th','Ts')]:
            data={'hands':[{'cards':[a,b],'bet_mult':1,'done':False,'from_split':False,'split_aces':False}],
                  'active_hand':0,'player':[a,b],'dealer':['2c','3d'],'deck':['4c','5d'],'phase':'player'}
            self.assertTrue(bj.can_split(data),(a,b))
            if (a,b)==('Jc','Kh'):
                outcome=bj.split(data)
                self.assertEqual(outcome,'continue');self.assertEqual(len(data['hands']),2)
        data={'hands':[{'cards':['9c','Kh'],'bet_mult':1,'done':False,'from_split':False,'split_aces':False}],
              'active_hand':0,'player':['9c','Kh'],'dealer':['2c','3d'],'deck':['4c','5d'],'phase':'player'}
        self.assertFalse(bj.can_split(data))

    def test_bomb_always_has_an_approachable_module(self):
        approachable={'Wire Matrix','Command Button','Rotary Synchronizer','Glyph Keypad'}
        for seed in range(100):
            d=e.bomb_new(random.Random(seed))
            self.assertTrue(any(module['title'] in approachable for module in d['modules']))

    def test_cluest_only_allows_logically_forced_calls_and_solves(self):
        for seed in range(25):
            d=e.cluest_new(random.Random(seed));self.assertEqual(len(d['names']),12)
            # Perfect logical play: repeatedly call one status that all remaining candidate worlds agree on.
            guard=0
            while len(d['known'])<12:
                forced,candidates=e.cluest_forced(d);self.assertTrue(candidates);self.assertTrue(forced)
                idx,status=next(iter(forced.items()))
                outcome=e.cluest_call(d,f'{e.cluest_label(idx)} {"criminal" if status else "innocent"}')
                self.assertIn(outcome,('revealed','solved'));guard+=1;self.assertLess(guard,20)
            self.assertEqual(len(d['known']),12)
        s,w=setup('cluest',0,1);g=start(s,w);now=1003
        while g['status']!='finished':
            forced,_=e.cluest_forced(g['data']);idx,status=next(iter(forced.items()))
            act(s,w,0,'cluest_call',f'{e.cluest_label(idx)} {"criminal" if status else "innocent"}',now);now+=1
        self.assertEqual(w['0']['coins'],105)

    def test_legacy_minigames_racer_is_retired_and_refunded(self):
        s={'games':{},'seen':{},'daily':{}}
        w={'0':{'name':'P0','coins':90.0,'points':0},'1':{'name':'P1','coins':90.0,'points':0}}
        g={'id':'old-race','kind':'racer','status':'playing','stake':10,'players':[{'id':'0','name':'P0'},{'id':'1','name':'P1'}],
           'reserved':{'0':10,'1':10},'paid':{},'winners':[],'left':[],'rev':3,'data':{}}
        s['games']['old-race']=g
        changed=e.retire_legacy_racer_games(s,w,1200)
        self.assertEqual(changed,1)
        self.assertEqual(g['status'],'finished')
        self.assertEqual([w['0']['coins'],w['1']['coins']],[100.0,100.0])
        self.assertIn('ChessBot',g['result'])

    def test_lobby_timeout_and_refund(self):
        s,w=setup('ships',5);g=start(s,w);e.tick(s,w,'g',1400,random.Random())
        self.assertEqual(g['status'],'finished');self.assertEqual(w['0']['coins'],100)
    def test_no_unauthorized_join_or_stakes(self):
        s,w=setup('connect');start(s,w)
        with self.assertRaises(ValueError):act(s,w,2,'join')
        with self.assertRaises(ValueError):e.create(s,w,'x','2','P','poker',10,1005)

    def test_blackjack_free_and_staked_settlement(self):
        # Force deterministic outcomes directly at the settlement boundary so
        # wallet maths is tested independently of shuffled deck order.
        s,w=setup('blackjack',0,1);g=start(s,w)
        g['data']['notice']='Player wins.'
        e._finish_blackjack(s,w,g,1003,'win')
        self.assertEqual(w['0']['coins'],102)
        self.assertEqual(w['0']['points'],123.5)

        s,w=setup('blackjack',10,1);g=start(s,w)
        # Starting the hand reserves the 10-coin stake.
        self.assertEqual(w['0']['coins'],90)
        g['data']['notice']='Player wins.'
        e._finish_blackjack(s,w,g,1003,'win')
        self.assertEqual(w['0']['coins'],110)
        self.assertEqual(g['paid']['0'],10)

        s,w=setup('blackjack',10,1);g=start(s,w)
        g['data']['notice']='Push.'
        e._finish_blackjack(s,w,g,1003,'push')
        self.assertEqual(w['0']['coins'],100)
        self.assertFalse(g['paid'])

    def test_blackjack_replay_same_or_changed_stake(self):
        s,w=setup('blackjack',10,1);g=start(s,w)
        g['data']['notice']='Push.'
        e._finish_blackjack(s,w,g,1003,'push')
        self.assertEqual(w['0']['coins'],100)

        # Same-stake replay reserves the original 10 coins again.
        e.action(s,w,'g','0','Player 0','replay','',1004,random.Random(7),bank())
        self.assertEqual(g['stake'],10)
        if g['status']!='finished':
            self.assertEqual(w['0']['coins'],90)
            g['data']['notice']='Push.';e._finish_blackjack(s,w,g,1005,'push')
        self.assertEqual(w['0']['coins'],100)

        # Change Stake lets the same finished table restart with a new amount.
        e.action(s,w,'g','0','Player 0','replay_stake','25',1006,random.Random(8),bank())
        self.assertEqual(g['stake'],25)
        if g['status']!='finished':self.assertEqual(w['0']['coins'],75)

        # Invalid custom stakes are rejected before any new hand starts.
        if g['status']!='finished':
            g['data']['notice']='Push.';e._finish_blackjack(s,w,g,1007,'push')
        with self.assertRaises(ValueError):
            e.action(s,w,'g','0','Player 0','replay_stake','51',1008,random.Random(9),bank())

    def test_chessle_normal_and_expert_rewards(self):
        import minigames_chessle as chessle
        # Unit tests must never depend on GitHub/raw-network availability. The
        # live bot loads the larger catalogue lazily; tests use the validated
        # built-in emergency pool.
        previous_attempted=chessle._REMOTE_LOAD_ATTEMPTED
        previous_openings=chessle.OPENINGS
        chessle._REMOTE_LOAD_ATTEMPTED=True
        chessle.OPENINGS=chessle._build_builtin_openings()
        try:
            for mode,reward in [('normal',3),('expert',6)]:
                s={'games':{},'seen':{},'daily':{}}
                w={'0':{'name':'Player 0','coins':100.,'points':123.5,'active_badge':''}}
                e.create(s,w,'g','0','Player 0','chessle',0,1000,mode=mode)
                g=start(s,w)
                target=' '.join(g['data']['target'])
                act(s,w,0,'guess',target,1003)
                self.assertEqual(g['status'],'finished')
                self.assertEqual(w['0']['coins'],100+reward)
                self.assertEqual(w['0']['points'],123.5)
        finally:
            chessle.OPENINGS=previous_openings
            chessle._REMOTE_LOAD_ATTEMPTED=previous_attempted

    def test_chessle_san_piece_letters_are_case_insensitive(self):
        import minigames_chessle as chessle
        parsed=chessle.parse_guess('e4 e5 nf3 nc6 bb5 a6','normal')
        self.assertEqual([move.casefold() for move in parsed], ['e4','e5','nf3','nc6','bb5','a6'])

    def test_chessle_feedback_is_duplicate_aware(self):
        import minigames_chessle as chessle
        self.assertEqual(
            chessle.grade(['Nf3','d5','Nf3','e6'], ['Nf3','Nf3','c4','e6']),
            ['G','Y','X','G'],
        )

    def test_blackjack_values(self):
        self.assertEqual(bj.hand_value(['As','Kh'])[0],21)
        self.assertEqual(bj.hand_value(['As','Ah','9c'])[0],21)
        self.assertEqual(bj.hand_value(['As','9h','5c'])[0],15)
        self.assertTrue(bj.is_blackjack(['As','Kh']))
        self.assertFalse(bj.is_blackjack(['As','5h','5c']))


class Poker(unittest.TestCase):
    def test_hand_ranks(self):
        self.assertGreater(p.rank('As Ks Qs Js Ts 2c 3d'.split()),p.rank('Ah Ad Ac As Ks 2d 3c'.split()))
        self.assertEqual(p.rank5('As 2d 3c 4h 5s'.split()),(4,5))
        self.assertGreater(p.rank5('Ah Ad Kc Kh Ks'.split()),p.rank5('2s 4s 6s 8s Ts'.split()))
    def test_side_pots_and_odd_chip(self):
        game={'stack':[0,0,0],'dealer':0,'total':[50,100,100],'folded':[False]*3,
              'holes':[['As','Ad'],['Ks','Kd'],['Qs','Qd']], 'board':['2c','4h','6s','8d','Tc']}
        p.showdown(game);self.assertEqual(game['stack'],[150,100,0])
        game={'stack':[0,0,0],'dealer':0,'total':[5,5,5],'folded':[False,False,True],
              'holes':[['2s','3d'],['4s','5d'],['6s','7d']], 'board':['As','Ks','Qs','Js','Ts']}
        p.showdown(game);self.assertEqual(game['stack'],[7,8,0])
    def test_heads_up_dealer_acts_first_preflop(self):
        game=p.new(2,random.Random(1));self.assertEqual(game['pending'][0],game['dealer'])
    def test_short_allin_does_not_reopen_raise(self):
        game=p.new(3,random.Random(1));i=game['pending'][0]
        p.act(game,i,'raise',100)
        j=game['pending'][0];p.act(game,j,'call')
        k=game['pending'][0];game['stack'][k]=110-game['bet'][k]
        p.act(game,k,'allin')
        self.assertFalse(p.can_raise(game,i))
        with self.assertRaises(ValueError):p.act(game,i,'raise',200)
    def test_random_tournaments_chip_conservation(self):
        for seed in range(30):
            rng=random.Random(seed);n=2+seed%5;game=p.new(n,rng)
            for step in range(1500):
                self.assertEqual(sum(game['stack'])+sum(game['total']),500*n,(seed,step,game))
                self.assertTrue(all(x>=0 for x in game['stack']))
                if game['phase']=='between':
                    if sum(x>0 for x in game['stack'])<=1:break
                    p.start(game,rng);continue
                if game['phase']=='finished':break
                self.assertTrue(game['pending'])
                i=game['pending'][0]
                choices=['call','fold']
                if p.can_raise(game,i):choices.append('allin')
                p.act(game,i,rng.choice(choices))
            else:self.fail('Tournament did not finish')

class Presentation(unittest.IsolatedAsyncioTestCase):
    async def test_every_view_embed_and_image(self):
        import minigames as ui
        import minigames_render as art
        from PIL import Image
        for kind in e.KINDS:
            s,w=setup(kind,n=1 if kind in ('mines','trivia','blackjack','chessle','lingo','math','codebreaker','cluest') else 2)
            g=s['games']['g'];ui.game_embed(g);v=ui.game_view(g);self.assertLessEqual(len(v.children),25);v.stop()
            start(s,w);v=ui.game_view(g);self.assertLessEqual(len(v.children),25);v.stop()
            image=art.png(g);width,height=Image.open(io.BytesIO(image)).size
            self.assertEqual(width,1200 if kind=='poker' else 820)
            self.assertGreaterEqual(height,600)
            self.assertLessEqual(height,1100)
            embed=ui.game_embed(g);self.assertLessEqual(len(embed.description),4096)
            e.finish(s,w,g,[],1005,'Cancelled',refund=True);ui.game_embed(g);art.png(g)
        s,w=setup('connect');g=s['games']['g'];e.finish(s,w,g,[],1005,'Expired',refund=True);ui.game_embed(g)
    async def test_mine_four_panels_form_uniform_5x16_board(self):
        import minigames as ui
        s,w=setup('mines',0,1);g=start(s,w)
        all_cells=[]
        for panel in range(4):
            view=ui.mine_panel_view(g,panel)
            self.assertEqual(len(view.children),4)
            self.assertEqual(view.total_children_count,24)
            cell_ids=[]
            for row in view.children:
                self.assertEqual(len(row.children),5)
                for item in row.children:
                    self.assertTrue(item.custom_id.startswith(f'mg:g:{g["rev"]}:minecell_'))
                    self.assertIsNone(item.label)
                    self.assertIsNotNone(item.emoji)
                    cell_ids.append(int(item.custom_id.rsplit('_',1)[1]))
            self.assertEqual(len(cell_ids),20)
            all_cells.extend(cell_ids)
            view.stop()
        self.assertEqual(sorted(all_cells),list(range(80)))

    async def test_poker_private_cards_not_in_public_image(self):
        import minigames_render as art
        s,w=setup('poker');g=start(s,w);original=art.png(g);changed=copy.deepcopy(g)
        changed['data']['holes'][0]=['As','Ad']
        self.assertEqual(original,art.png(changed))
        self.assertNotEqual(art.png(g,'0'),art.png(changed,'0'))
    async def test_ship_positions_hidden_from_public(self):
        import minigames_render as art
        s,w=setup('ships');g=start(s,w);original=art.png(g);changed=copy.deepcopy(g)
        changed['data']['fleets'][0]=e.fleet(random.Random(999))
        self.assertEqual(original,art.png(changed))
        self.assertNotEqual(art.png(g,'0'),art.png(changed,'0'))

class Storage(unittest.TestCase):
    """Exercise real Git commits against an isolated LOCAL bare repository."""
    def setUp(self):
        import os,tempfile,subprocess,json
        from pathlib import Path
        self.os=os;self.oldcwd=os.getcwd();self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.oldenv={k:os.environ.get(k) for k in ['MINIGAMES_STATE_KEY','GITHUB_REF_NAME']}
        os.environ['MINIGAMES_STATE_KEY']='test-only-secret-not-a-production-key-0123456789';os.environ['GITHUB_REF_NAME']='main'
        def git(*args,cwd=None):
            return subprocess.run(['git',*args],cwd=cwd or self.path,capture_output=True,text=True,check=True)
        self.git=git
        git('init','--bare',str(self.path/'remote.git'))
        git('clone',str(self.path/'remote.git'),str(self.path/'work'))
        os.chdir(self.path/'work');git('checkout','-b','main',cwd=os.getcwd())
        git('config','user.name','Test',cwd=os.getcwd());git('config','user.email','test@example.invalid',cwd=os.getcwd())
        Path('shared_leaderboard.json').write_text(json.dumps({'0':{'name':'P0','coins':100,'points':12},'1':{'name':'P1','coins':100,'points':7}}))
        git('add','.',cwd=os.getcwd());git('commit','-m','Initial',cwd=os.getcwd());git('push','-u','origin','main',cwd=os.getcwd())
    def tearDown(self):
        self.os.chdir(self.oldcwd)
        for k,v in self.oldenv.items():
            if v is None:self.os.environ.pop(k,None)
            else:self.os.environ[k]=v
        self.temp.cleanup()
    def test_atomic_wager_restart_and_duplicate_push_ack(self):
        from unittest.mock import patch
        import minigames_store as st
        ledger=st.ledger
        def create(s,w):
            e.create(s,w,'g','0','P0','connect',10,1000)
            e.action(s,w,'g','1','P1','join','',1001,random.Random(),[])
        st.transact('create',create)
        before=ledger._push_files;lost=[False]
        def uncertain(files,message):
            result=before(files,message)
            if result and not lost[0]:lost[0]=True;return False
            return result
        with patch.object(ledger,'_push_files',uncertain):
            st.transact('start',lambda s,w:e.action(s,w,'g','0','P0','start','',1002,random.Random(),[]))
        self.assertTrue(lost[0]);self.assertTrue(st.read()['games']['g']['reserved'])
        calls=[];st.transact('start',lambda s,w:calls.append('duplicate'))
        self.assertEqual(calls,[])
        wallets,_=ledger._origin_state();self.assertEqual(wallets['0']['coins'],90);self.assertEqual(wallets['1']['coins'],90)
        st.transact('finish',lambda s,w:e.finish(s,w,s['games']['g'],['0'],1005,'Win'))
        st.transact('finish',lambda s,w:e.finish(s,w,s['games']['g'],['0'],1005,'Win'))
        ledger._fetch_retry();wallets,_=ledger._origin_state()
        self.assertEqual((wallets['0']['coins'],wallets['1']['coins']),(110,90))
        self.assertEqual((wallets['0']['points'],wallets['1']['points']),(12,7))
    def test_insufficient_balance_rolls_back_and_key_is_required(self):
        import minigames_store as st
        def fail(s,w):
            e.create(s,w,'g','0','P0','connect',50,1000)
            e.action(s,w,'g','1','P1','join','',1001,random.Random(),[])
            w['1']['coins']=1
            e.action(s,w,'g','0','P0','start','',1002,random.Random(),[])
        with self.assertRaises(ValueError):st.transact('fail',fail)
        self.assertEqual(st.read()['games'],{})
        st.transact('secret',lambda s,w:s.update(hidden='As Ks secret fleet'))
        raw=st.ledger._origin_file(st.STATE_FILE)
        self.assertNotIn('As Ks',raw)
        self.os.environ['MINIGAMES_STATE_KEY']='different-test-only-key-00000000000000000000'
        with self.assertRaises(RuntimeError):st.read()
    def test_concurrent_other_writer_preserved(self):
        from unittest.mock import patch
        from pathlib import Path
        import json
        import minigames_store as st
        st.transact('create',lambda s,w:e.create(s,w,'g','0','P0','mines',0,1000))
        original=st.ledger._push_files;injected=[False]
        other=self.path/'other';self.git('clone','--branch','main',str(self.path/'remote.git'),str(other))
        self.git('config','user.name','Other',cwd=other);self.git('config','user.email','other@example.invalid',cwd=other)
        def conflict(files,message):
            if not injected[0]:
                injected[0]=True;path=other/'shared_leaderboard.json';data=json.loads(path.read_text());data['1']['coins']+=7
                path.write_text(json.dumps(data));self.git('add','.',cwd=other);self.git('commit','-m','Concurrent coins',cwd=other);self.git('push','origin','main',cwd=other)
            return original(files,message)
        with patch.object(st.ledger,'_push_files',conflict):
            st.transact('reward',lambda s,w:w['0'].update(coins=w['0']['coins']+1))
        st.ledger._fetch_retry();wallets,_=st.ledger._origin_state()
        self.assertEqual(wallets['0']['coins'],101);self.assertEqual(wallets['1']['coins'],107)

class Interactions(unittest.IsolatedAsyncioTestCase):
    async def test_lobby_join_start_and_actual_column_button(self):
        import minigames as ui
        import discord
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        app=ui.Arcade();app.state={'games':{},'seen':{},'daily':{}};app.ready_event.set();app.bank=bank()
        wallets={'0':{'name':'P0','coins':100.,'points':4},'1':{'name':'P1','coins':100.,'points':8}}
        async def save(tx,callback):
            ss,ww=copy.deepcopy(app.state),copy.deepcopy(wallets)
            callback(ss,ww);app.state=ss;wallets.clear();wallets.update(ww)
        app.save=save;app.refresh_one=AsyncMock()
        def interaction(uid,cid,custom,value=None):
            response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock(),send_modal=AsyncMock(),is_done=lambda:True)
            data={'custom_id':custom}
            if value is not None:data['components']=[{'components':[{'custom_id':'value','value':value}]}]
            return SimpleNamespace(id=cid,channel_id=ui.CHANNEL_ID,user=SimpleNamespace(id=uid,display_name='P'+str(uid),bot=False),
                                   data=data,type=discord.InteractionType.modal_submit if value is not None else discord.InteractionType.component,
                                   response=response,followup=SimpleNamespace(send=AsyncMock()))
        i=interaction(0,991,'mg:pick:connect','10');await app.on_interaction(i)
        self.assertIn('991',app.state['games']);self.assertEqual(wallets['0']['coins'],100)
        i=interaction(1,992,'mg:991:0:join');await app.on_interaction(i)
        self.assertEqual(len(app.state['games']['991']['players']),2)
        app.last_click.clear();i=interaction(0,993,'mg:991:1:start');await app.on_interaction(i)
        self.assertEqual(wallets['0']['coins'],90)
        app.last_click.clear();i=interaction(0,994,'mg:991:2:drop_0');await app.on_interaction(i)
        self.assertEqual(app.state['games']['991']['data']['board'][35],1)
        self.assertEqual(wallets['0']['points'],4)
        self.assertEqual(app.refresh_one.await_count,4)
        app.last_click.clear();i=interaction(2,995,'mg:991:3:private');await app.on_interaction(i)
        self.assertIn('only for players',i.followup.send.await_args.args[0])
        await app.close()

class ConcurrentControls(unittest.TestCase):
    def test_both_rps_players_can_use_same_embed(self):
        s,w=setup('rps');g=start(s,w);revision=g['rev']
        for uid,value in [('0','rock'),('1','scissors')]:
            e.action(s,w,'g',uid,'P'+uid,'choose',value,1003,random.Random(),[],expected=revision)
        self.assertEqual(g['status'],'finished')
    def test_trivia_same_round_accepts_peers_previous_round_rejected(self):
        s,w=setup('trivia');g=start(s,w);revision=g['rev']
        for uid in ['0','1']:e.action(s,w,'g',uid,'P'+uid,'answer',0,1003,random.Random(),[],expected=revision)
        self.assertTrue(g['data']['reveal']);e.tick(s,w,'g',1011,random.Random())
        with self.assertRaises(ValueError):e.action(s,w,'g','0','P','answer',0,1012,random.Random(),[],expected=revision)

class ContentPack(unittest.TestCase):
    def test_bundled_trivia_is_large_unique_and_valid(self):
        import json
        from pathlib import Path
        questions=json.loads(Path(__file__).with_name('minigames_trivia.json').read_text())['questions']
        self.assertGreaterEqual(len(questions),1000)
        self.assertEqual(len({q['id'] for q in questions}),len(questions))
        for q in questions:
            self.assertEqual(len(set(q['options'])),4);self.assertIn(q['answer'],q['options'])
            self.assertIn(q['difficulty'],['easy','medium','hard'])
        self.assertTrue(all(sum(q['difficulty']==d for q in questions)>=100 for d in ['easy','medium','hard']))
    def test_badges_are_valid_images(self):
        import base64
        from PIL import Image
        from minigames_badges import DATA
        self.assertGreater(len(DATA),560)
        for key,value in DATA.items():
            im=Image.open(io.BytesIO(base64.b64decode(value)));im.verify()

class StatusSafety(unittest.IsolatedAsyncioTestCase):
    async def test_status_is_health_check_only_and_never_reads_game_state(self):
        import discord,minigames as ui
        from unittest.mock import AsyncMock
        from types import SimpleNamespace

        app=ui.Arcade();app.ready_event.set()
        def interaction(uid,data,kind=discord.InteractionType.application_command):
            return SimpleNamespace(
                user=SimpleNamespace(id=uid,bot=False),
                channel_id=ui.CHANNEL_ID,
                data=data,
                type=kind,
                response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
                edit_original_response=AsyncMock(),
            )

        # /status is only a health check, even for Sharkmeister.
        app.state=None
        for uid in (123,362606514764251137):
            i=interaction(uid,{'name':'status'})
            await app.on_interaction(i)
            self.assertEqual(i.response.send_message.await_count,1)
            self.assertTrue(i.response.send_message.await_args.kwargs['ephemeral'])
            self.assertEqual(i.response.send_message.await_args.args[0],'✅ Minigames is online.')
            self.assertEqual(i.response.defer.await_count,0)
            self.assertEqual(i.followup.send.await_count,0)
            self.assertEqual(i.edit_original_response.await_count,0)

        # Old private-status component IDs are no longer handled.
        i=interaction(362606514764251137,{'custom_id':'mgadmin:game:g'},discord.InteractionType.component)
        await app.on_interaction(i)
        self.assertEqual(i.response.send_message.await_count,0)
        self.assertEqual(i.response.defer.await_count,0)
        self.assertEqual(i.followup.send.await_count,0)
        self.assertEqual(i.edit_original_response.await_count,0)
        await app.close()


class LeavingGames(unittest.TestCase):
    def test_group_leave_frees_player_without_reward(self):
        for kind in ('trivia','escape','poker'):
            s,w=setup(kind,n=3);g=start(s,w)
            act(s,w,0,'leave')
            self.assertIn('0',g['left'])
            self.assertEqual(w['0']['coins'],100)
            e.create(s,w,'new','0','Player 0','mines',0,1004)
            self.assertEqual(g['status'],'playing')
            with self.assertRaises(ValueError):act(s,w,0,'leave')
    def test_last_group_players_can_close(self):
        for kind in ('trivia','escape','poker'):
            s,w=setup(kind,n=2);g=start(s,w)
            act(s,w,0,'leave')
            if g['status']!='finished':act(s,w,1,'leave')
            self.assertEqual(g['status'],'finished')
            self.assertTrue(all(x['coins']==100 for x in w.values()))
    def test_departed_poker_seat_stays_out_next_hand(self):
        import minigames_poker as poker
        s,w=setup('poker',n=3);g=start(s,w);act(s,w,0,'leave');d=g['data']
        for _ in range(12):
            if d['phase']=='between':break
            poker.act(d,d['pending'][0],'fold');poker.skip_departed(d)
        self.assertEqual(d['phase'],'between')
        poker.start(d,random.Random(44));poker.skip_departed(d)
        self.assertTrue(d['folded'][0]);self.assertNotIn(0,d['pending'])
    def test_quiz_departed_player_does_not_block_round(self):
        s,w=setup('trivia',n=2);g=start(s,w)
        act(s,w,0,'leave');act(s,w,1,'answer',0)
        self.assertTrue(g['data']['reveal'])


class RewardUpdate(unittest.TestCase):
    def test_mine_stakes_rejected_and_old_reservations_refunded_once(self):
        s,w=setup('mines',0,1);g=start(s,w)
        with self.assertRaises(ValueError):e.create(s,w,'bad','1','P1','mines',30,1005)
        g['stake']=30;g['reserved']=True;w['0']['coins']=70
        e.retire_mine_stakes(s,w,1010);e.retire_mine_stakes(s,w,1011)
        self.assertEqual(w['0']['coins'],100)
        self.assertEqual(w['0']['points'],123.5)
        self.assertEqual(g['status'],'finished')
        self.assertFalse(g['paid'])
    def test_legacy_mine_lobby_cannot_start_with_stake(self):
        s,w=setup('mines',0,1);s['games']['g']['stake']=30
        with self.assertRaises(ValueError):start(s,w)
        self.assertEqual(w['0']['coins'],100)
    def test_trivia_block_rewards(self):
        self.assertEqual(e.trivia_coins([5,0,0]),1)
        self.assertEqual(e.trivia_coins([4,4,4]),4)
        self.assertEqual(e.trivia_coins([5,5,5]),10)
        self.assertEqual(e.trivia_coins([2,2,2]),0)
    def test_easy_block_only_earns_one_coin(self):
        s,w=setup('trivia',0,1);g=start(s,w);now=1004
        for j in range(15):
            q=g['data']['questions'][j];correct=q['options'].index(q['answer'])
            act(s,w,0,'answer',correct if j<5 else (correct+1)%4,now)
            if j<14:now+=8;e.tick(s,w,'g',now,random.Random(1));now+=1
        self.assertEqual(w['0']['coins'],101)
        self.assertEqual(w['0']['points'],123.5)


class LeaveConfirmation(unittest.IsolatedAsyncioTestCase):
    async def test_discord_valid_confirmation_and_leave_for_every_game(self):
        import discord,minigames as ui
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        for kind in e.KINDS:
            s,w=setup(kind,n=1 if kind in ('mines','blackjack','chessle','lingo','math','codebreaker','cluest') else 2);g=start(s,w)
            app=ui.Arcade();app.state=s;app.ready_event.set();app.bank=bank()
            async def save(tx,callback):
                ss,ww=copy.deepcopy(app.state),copy.deepcopy(w)
                callback(ss,ww);app.state=ss;w.clear();w.update(ww)
            app.save=save;app.refresh_one=AsyncMock()
            def interaction(value=None):
                data={'custom_id':f'mg:g:{g["rev"]}:confirm_leave'}
                if value is not None:data['components']=[{'components':[{'custom_id':'value','value':value}]}]
                return SimpleNamespace(id=555,channel_id=ui.CHANNEL_ID,user=SimpleNamespace(id=0,display_name='Player 0',bot=False),data=data,
                    type=discord.InteractionType.modal_submit if value is not None else discord.InteractionType.component,
                    response=SimpleNamespace(send_modal=AsyncMock(),defer=AsyncMock(),send_message=AsyncMock(),is_done=lambda:True),
                    followup=SimpleNamespace(send=AsyncMock()))
            click=interaction();await app.on_interaction(click)
            click.response.send_modal.assert_awaited_once()
            modal=click.response.send_modal.await_args.args[0]
            self.assertLessEqual(len(modal.title),45)
            for field in modal.children:
                self.assertLessEqual(len(field.label),45)
                self.assertLessEqual(len(field.placeholder or ''),100)
            modal.stop();app.last_click.clear()
            submit=interaction('FORFEIT');await app.on_interaction(submit)
            self.assertEqual(submit.followup.send.await_count,0)
            self.assertEqual(app.refresh_one.await_count,1)
            e.create(app.state,w,'next','0','Player 0','mines',0,1006)
            self.assertEqual(w['0']['points'],123.5)
            await app.close()


class LatestDropAndUnlimitedCoins(unittest.TestCase):
    def test_latest_drop_tracks_only_the_new_disc(self):
        s,w=setup('connect');g=start(s,w)
        act(s,w,0,'drop',3);self.assertEqual(g['data']['last_drop'],38)
        act(s,w,1,'drop',3);self.assertEqual(g['data']['last_drop'],31)
        before=copy.deepcopy(g)
        with self.assertRaises(ValueError):act(s,w,1,'drop',0)
        self.assertEqual(g,before)
    def test_old_daily_usage_does_not_cap_new_wins(self):
        s,w=setup('connect');g=start(s,w)
        s['daily']={'1970-01-01:0':300}
        for i,col in enumerate([0,1,0,1,0,1,0]):act(s,w,i%2,'drop',col)
        self.assertEqual(w['0']['coins'],105)
        e.finish(s,w,g,['0'],1010,'duplicate')
        self.assertEqual(w['0']['coins'],105)
        self.assertEqual(w['0']['points'],123.5)

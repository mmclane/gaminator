import random

from gaminator.games.assassin.services.chain import build_assignments, build_chain


def test_chain_is_single_cycle():
    ids = list(range(1, 11))
    chain = dict(build_chain(ids, random.Random(3)))
    assert set(chain) == set(ids)
    assert set(chain.values()) == set(ids)
    assert all(p != t for p, t in chain.items())
    # follow the chain: it visits everyone before returning
    seen, cur = [], ids[0]
    while cur not in seen:
        seen.append(cur)
        cur = chain[cur]
    assert len(seen) == len(ids)


def test_two_players_target_each_other():
    chain = dict(build_chain([1, 2], random.Random(0)))
    assert chain == {1: 2, 2: 1}


def test_assignments_use_distinct_words():
    words8 = list("abcdefgh")
    out = build_assignments([1, 2, 3, 4], words8, random.Random(5))
    words = [w for _, _, w, _ in out] + [b for _, _, _, b in out]
    assert len(set(words)) == 8

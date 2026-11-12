"""Extend independent projection arithmetic with source-supported channel labels."""
from expected_v1 import derive as base_derive,channel,COLS,tier

def derive(raw):
 result=base_derive(raw)
 result['possible_source_channels']=sorted({channel(r['attribution_channel'],r['utm_source'],r['utm_medium'])for r in raw['orders']})
 return result

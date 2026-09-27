def explain(s):
    lines=[f'{s.symbol}: {s.state.value}']
    if s.trend: lines.append('Trend: '+('BULLISH' if s.trend==1 else 'BEARISH'))
    if s.support is not None: lines.append(f'Box: {s.support:.4f} – {s.resistance:.4f}')
    if s.breakout_score: lines.append(f'Breakout score: {s.breakout_score}/5')
    if s.evidence: lines.append('Evidence: '+'; '.join(s.evidence))
    if s.state.value in ('BUY','SELL'): lines += [f'Entry: {s.entry:.4f}',f'Stop: {s.stop:.4f}',f'Target: {s.target:.4f}','Rule-based setup explanation; not a guaranteed prediction.']
    elif s.state.value=='ACCEPTED_RETEST_WATCH': lines.append('Acceptance confirmed; waiting for a controlled retest.')
    elif s.state.value=='ACCEPTANCE_WATCH': lines.append('Breakout detected; acceptance is being evaluated.')
    return '\n'.join(lines)

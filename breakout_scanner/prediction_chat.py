def explain(s):
    lines=[f'{s.symbol}: {s.state.value}']
    if s.trend: lines.append('Trend: '+('BULLISH' if s.trend==1 else 'BEARISH'))
    if s.support is not None and s.resistance is not None: lines.append(f'Box: {s.support:.4f} – {s.resistance:.4f}')
    score = getattr(s, 'bias_score', getattr(s, 'breakout_score', 0))
    if score: lines.append(f'Bias score: {score}/3')
    if s.evidence: lines.append('Evidence: '+'; '.join(s.evidence))
    if s.state.value in ('BUY','SELL'):
        if s.entry is not None: lines.append(f'Entry: {s.entry:.4f}')
        if s.stop is not None: lines.append(f'Stop: {s.stop:.4f}')
        if s.target is not None: lines.append(f'Target: {s.target:.4f}')
        lines.append('Rule-based setup explanation; not a guaranteed prediction.')
    elif s.state.value=='ACCEPTED_RETEST_WATCH': lines.append('Acceptance confirmed; waiting for a controlled retest.')
    elif s.state.value=='ACCEPTANCE_WATCH': lines.append('Breakout detected; acceptance is being evaluated.')
    return '\n'.join(lines)

# GTA VI Twitch CCU forecast — entry

`main.py` (Python 3.13, standard library only) forecasts hours 2–24 of a
launch day from hour 1's Twitch viewer print. It learns the normalised
launch-day shape (viewers / hour-1 print, per UTC hour) at run time from
the case's comparables, keeping only launches whose hour 1 was already
at least 15% of their release-day peak (rolling midnight-local unlocks,
like GTA VI's), blends the empirical log-ratio quantiles with a normal
fit, shrinks them 10% toward persistence, and widens the spread 15%.

Leave-one-out backtest over the comparables in the kit's practice cases
(each comparable forecast from the others, scored with the contest's
mean log pinball loss): 0.2214 vs persistence 0.2652 on the 24
rolling-unlock comparables (−16.5%), and 0.8728 vs 0.9297 over all 37
usable comparables (−6.1%).

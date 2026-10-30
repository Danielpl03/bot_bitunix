-- Optional manual migration. The bot also creates these tables automatically
-- on startup when TTR_DB_BACKEND=postgres.
CREATE TABLE IF NOT EXISTS public.ttr_live_config (
  symbol TEXT PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  config TEXT NOT NULL,
  last_bar_time BIGINT,
  state TEXT NOT NULL DEFAULT '{}',
  updated_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS public.ttr_live_events (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  event_time BIGINT NOT NULL,
  event_type TEXT NOT NULL,
  side TEXT,
  price DOUBLE PRECISION,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ttr_live_events_symbol_id ON public.ttr_live_events(symbol, id DESC);
CREATE TABLE IF NOT EXISTS public.ttr_live_trades (
  id BIGSERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  entry_time BIGINT NOT NULL,
  entry_price DOUBLE PRECISION NOT NULL,
  exit_time BIGINT,
  exit_price DOUBLE PRECISION,
  exit_reason TEXT,
  status TEXT NOT NULL,
  stop_price DOUBLE PRECISION,
  realized_pnl_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
  remaining_pct DOUBLE PRECISION NOT NULL DEFAULT 100,
  payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ttr_live_trades_symbol_id ON public.ttr_live_trades(symbol, id DESC);

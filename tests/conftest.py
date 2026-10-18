import os


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_JWT_ISSUER", "https://example.supabase.co/auth/v1")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-only-secret")

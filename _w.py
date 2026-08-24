import io,os,sys
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
os.environ.setdefault('ENABLE_SCHEDULER','false')
from dotenv import load_dotenv; load_dotenv()
import supabase_client as sc
c=sc.get_supabase_client()
n=c.table('screened_latest').select('company_code',count='exact').not_.is_('delisted_at','null').execute()
print('印が付いた銘柄:', n.count, '件')
rows=(c.table('screened_latest').select('company_code,company_name,delisted_at')
      .not_.is_('delisted_at','null').order('delisted_at').limit(40).execute().data or [])
for r in rows: print(f"  {r['company_code']:<6}{str(r.get('company_name'))[:20]:<22}{str(r['delisted_at'])[:10]}")

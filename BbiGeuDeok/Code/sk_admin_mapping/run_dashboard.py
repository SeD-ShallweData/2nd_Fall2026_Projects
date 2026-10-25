"""Local dashboard with real LLM enabled explicitly by this launcher; --structured opts out."""
import argparse,os
def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8766);p.add_argument('--structured',action='store_true');a=p.parse_args()
    os.environ['ROA_ENABLE_LLM']='0' if a.structured else '1'
    os.environ['ROA_USE_WELFARE_API_CACHE']='1'
    from spatial_explorer import serve
    serve(a.port)
if __name__=='__main__':main()

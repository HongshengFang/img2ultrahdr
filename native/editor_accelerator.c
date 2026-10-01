/* Exact double-precision geodesic scans. No fast-math or fused arithmetic. */
#include <math.h>
#include <stdlib.h>
#include <stdint.h>
#include <dispatch/dispatch.h>

static void transpose(const float *src,float *dst,int h,int w) {
    for (int by=0;by<h;by+=32) for (int bx=0;bx<w;bx+=32)
        for (int y=by;y<h && y<by+32;++y) for (int x=bx;x<w && x<bx+32;++x)
            dst[(size_t)x*h+y]=src[(size_t)y*w+x];
}

int hdr_envelopes(const float *guide, float *lower, float *upper, int h, int w,
                  double slope, int *iterations, double *change) {
    const int nmax = h > w ? h : w;
    const int workers=6;
    double *buf = malloc((size_t)nmax * 5 * workers * sizeof(double));
    float *planes=malloc((size_t)h*w*3*sizeof(float));
    if (!buf || !planes) { free(buf);free(planes);return -1; }
    float *gt=planes,*lt=gt+(size_t)h*w,*ut=lt+(size_t)h*w;
    transpose(guide,gt,h,w);
    // Preview-sized graphs revisit the same edge distances each iteration.
    // Cache the exact float64 prefix sums; full-size graphs stay memory-bounded.
    double *distances = (size_t)h*w <= 4*1024*1024 ? malloc((size_t)h*w*2*sizeof(double)) : NULL;
    if (distances) {
        for (int axis=1;axis>=0;--axis) {
            int lines=axis ? h : w,count=axis ? w : h;
            const float *gp=axis ? guide : gt;
            double *dp=distances+(axis ? 0 : (size_t)h*w);
            dispatch_apply(workers,dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0), ^(size_t worker) {
                for (int line=(int)(worker*lines/workers);line<(int)((worker+1)*lines/workers);++line) {
                    size_t base=(size_t)line*count; dp[base]=0;
                    for (int j=1;j<count;++j)
                        dp[base+j]=dp[base+j-1]+slope*fabs((double)gp[base+j]-(double)gp[base+j-1]);
                }
            });
        }
    }

    for (int iteration=0; iteration<512; ++iteration) {
        double maximum=0;
        for (int axis=1;axis>=0;--axis) {
            int lines=axis ? h : w, count=axis ? w : h;
            const float *gp=axis ? guide : gt;
            float *lp=axis ? lower : lt,*up=axis ? upper : ut;
            if (!axis) { transpose(lower,lt,h,w);transpose(upper,ut,h,w); }
            size_t step=1;
            double changes[6]={0};
            double *changes_ptr=changes;
            dispatch_apply(workers,dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0), ^(size_t worker) {
              double *d=buf+worker*(size_t)nmax*5,*a=d+nmax,*b=a+nmax,*lo=b+nmax,*hi=lo+nmax;
              double local_maximum=0;
              int first=(int)(worker*lines/workers),last=(int)((worker+1)*lines/workers);
              for (int line=first;line<last;++line) {
                size_t base=(size_t)line*count;
                double *prefix=distances ? distances+(axis ? 0 : (size_t)h*w)+base : d;
                if (!distances) prefix[0]=0;
                for (int j=0;j<count;++j) {
                    size_t k=base+(size_t)j*step;
                    if (j && !distances) prefix[j]=prefix[j-1]+slope*fabs((double)gp[k]-(double)gp[k-step]);
                    a[j]=lp[k]; b[j]=up[k];
                }
                double amin=INFINITY,bmax=-INFINITY;
                for (int j=0;j<count;++j) {
                    amin=fmin(amin,a[j]-prefix[j]); bmax=fmax(bmax,b[j]+prefix[j]);
                    lo[j]=prefix[j]+amin; hi[j]=-prefix[j]+bmax;
                }
                amin=INFINITY;bmax=-INFINITY;
                for (int j=count-1;j>=0;--j) {
                    amin=fmin(amin,a[j]+prefix[j]); bmax=fmax(bmax,b[j]-prefix[j]);
                    double newlo=fmin(lo[j],-prefix[j]+amin),newhi=fmax(hi[j],prefix[j]+bmax);
                    local_maximum=fmax(local_maximum,fmax(a[j]-newlo,newhi-b[j]));
                    size_t k=base+(size_t)j*step;
                    lp[k]=(float)newlo;up[k]=(float)newhi;
                }
            }
              changes_ptr[worker]=local_maximum;
            });
            for (int worker=0;worker<workers;++worker) maximum=fmax(maximum,changes[worker]);
            if (!axis) { transpose(lt,lower,w,h);transpose(ut,upper,w,h); }
        }
        *iterations=iteration+1;*change=maximum;
        if (maximum<2e-7) break;
    }
    free(buf);free(planes);free(distances);return 0;
}

void hdr_matmul(const float *x, const float *m, float *out, size_t n, int mode) {
    for (size_t k=0;k<n;++k) {
        for (int j=0;j<3;++j) {
            float a=x[3*k],b=x[3*k+1],c=x[3*k+2];
            const float *v=m+3*j;
            if (mode==1) out[3*k+j]=fmaf(c,v[2],fmaf(b,v[1],a*v[0]));
            else if (mode==2) out[3*k+j]=fmaf(a,v[0],fmaf(b,v[1],c*v[2]));
            else if (mode==3) out[3*k+j]=(float)((double)a*v[0]+(double)b*v[1]+(double)c*v[2]);
            else out[3*k+j]=(a*v[0]+b*v[1])+c*v[2];
        }
    }
}

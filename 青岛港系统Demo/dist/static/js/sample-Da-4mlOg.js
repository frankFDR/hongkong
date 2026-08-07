/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{b as u}from"./_baseRest-BC7vAhLA.js";import{bM as c,bT as g,dx as m,dy as d,bL as A}from"./index-BejtHq3K.js";import{b as x}from"./_baseIndexOf-a0daFAai.js";import{b as w}from"./_baseRandom-CgWnUD7v.js";function O(n,e,t,i){for(var l=t-1,s=n.length;++l<s;)if(i(n[l],e))return l;return-1}var v=Array.prototype,a=v.splice;function M(n,e,t,i){var l=i?O:x,s=-1,o=e.length,r=n;for(n===e&&(e=c(e)),t&&(r=g(n,m(t)));++s<o;)for(var f=0,p=e[s],h=t?t(p):p;(f=l(r,h,f,i))>-1;)r!==n&&a.call(r,f,1),a.call(n,f,1);return n}function P(n,e){return n&&n.length&&e&&e.length?M(n,e):n}var W=u(P);function b(n){var e=n.length;return e?n[w(0,e-1)]:void 0}function R(n){return b(d(n))}function j(n){var e=A(n)?b:R;return e(n)}export{P as a,M as b,W as p,j as s};

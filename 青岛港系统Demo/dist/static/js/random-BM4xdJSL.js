/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{b as m}from"./_baseRandom-CgWnUD7v.js";import{i as s}from"./_isIterateeCall-D3FQcQ8X.js";import{ab as f}from"./index-BejtHq3K.js";var e=parseFloat,b=Math.min,v=Math.random;function M(i,n,a){if(a&&typeof a!="boolean"&&s(i,n,a)&&(n=a=void 0),a===void 0&&(typeof n=="boolean"?(a=n,n=void 0):typeof i=="boolean"&&(a=i,i=void 0)),i===void 0&&n===void 0?(i=0,n=1):(i=f(i),n===void 0?(n=i,i=0):n=f(n)),i>n){var t=i;i=n,n=t}if(a||i%1||n%1){var d=v();return b(i+d*(n-i+e("1e-"+((d+"").length-1))),n)}return m(i,n)}export{M as r};

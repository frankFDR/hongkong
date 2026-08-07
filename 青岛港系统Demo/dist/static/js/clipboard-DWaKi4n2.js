/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{fd as a,fe as c,ff as e}from"./index-BejtHq3K.js";const i=s=>{e.$baseMessage("拷贝".concat(s,"成功"),"success","hey")},p=s=>{e.$baseMessage("拷贝".concat(s,"失败"),"error","hey")},b=s=>{const{isSupported:r,copy:o}=a({legacy:!0});r||c("clipboard-write"),o(s).then(()=>{i(s)}).catch(()=>{p(s)})};export{b as h};

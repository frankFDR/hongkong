/*!  build: Vue Shop Vite 
     copyright: https://vuejs-core.cn/shop-vite   
     time: 2025-07-03 09:54:42 
 */
import{af as c,ag as p,ai as u,d as i,an as f,X as v,c as s,f as a,p as m,v as o,u as r,a2 as y,n as S,aF as _}from"./index-BejtHq3K.js";const b=c({direction:{type:String,values:["horizontal","vertical"],default:"horizontal"},contentPosition:{type:String,values:["left","center","right"],default:"center"},borderStyle:{type:p(String),default:"solid"}}),g=i({name:"ElDivider"}),h=i({...g,props:b,setup(n){const l=n,e=f("divider"),d=v(()=>e.cssVar({"border-style":l.borderStyle}));return(t,z)=>(a(),s("div",{class:o([r(e).b(),r(e).m(t.direction)]),style:S(r(d)),role:"separator"},[t.$slots.default&&t.direction!=="vertical"?(a(),s("div",{key:0,class:o([r(e).e("text"),r(e).is(t.contentPosition)])},[y(t.$slots,"default")],2)):m("v-if",!0)],6))}});var P=u(h,[["__file","divider.vue"]]);const k=_(P);export{k as E};

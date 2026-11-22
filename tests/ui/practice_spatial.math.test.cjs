const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const source=path.resolve(__dirname,'../../src/aiforstudy/resources/practice/practice-spatial.js');
function api(){ assert.ok(fs.existsSync(source),'spatial math and renderer must exist'); return require(source); }
function near(a,b,eps=1e-10){assert.ok(Math.abs(a-b)<eps,`${a} != ${b}`)}
test('textbook stereographic map lies on sphere centered at (0,0,1) and on segment NP',()=>{
  const {stereographic}=api();
  for(const [x,y] of [[0,0],[1,0],[2,0],[0,2],[-2,3],[1e6,-2e6]]){
    const p=stereographic(x,y);
    near(p[0]*p[0]+p[1]*p[1]+(p[2]-1)*(p[2]-1),1);
    near(-2*p[0]-(p[2]-2)*x,0,1e-9);
    near(-2*p[1]-(p[2]-2)*y,0,1e-9);
    const segmentParameter=(2-p[2])/2;
    assert.ok(segmentParameter>0 && segmentParameter<=1,'Q must be on the north-to-input segment, including r<2');
    assert.ok(p[2]<2,'finite plotted radii remain below north pole');
  }
  assert.deepEqual(stereographic(0,0),[0,0,0]);
  assert.deepEqual(stereographic(1,0),[0.8,0,0.4]);
  assert.deepEqual(stereographic(2,0),[1,0,1]);
  assert.deepEqual(stereographic(0,2),[0,1,1]);
});
test('textbook inverse recovers z and radius two is exactly the equator',()=>{
  const {stereographic}=api();
  for(const [x,y] of [[0,0],[0.5,0.5],[1,0],[2,0],[-3,4]]){
    const [xi,eta,zeta]=stereographic(x,y);
    near(2*xi/(2-zeta),x);near(2*eta/(2-zeta),y);
    near(Math.hypot(xi,eta,zeta-2),4/Math.sqrt(x*x+y*y+4));
  }
  near(stereographic(Math.sqrt(2),Math.sqrt(2))[2],1);
});
test('one logarithm turn keeps z and adds 2pi to imaginary value',()=>{
  const {logarithmPoint}=api();
  const first=logarithmPoint(2,0.4), next=logarithmPoint(2,0.4+2*Math.PI);
  near(first.x,next.x);near(first.y,next.y);near(next.imag-first.imag,2*Math.PI);
  near(first.real,Math.log(2));near(first.principal,next.principal);
});
test('analytic principal logarithm excludes negative ray and zero without deleting continuous lift',()=>{
  const {logarithmPoint}=api();
  assert.throws(()=>logarithmPoint(0,0),RangeError);
  for(const theta of [Math.PI,-Math.PI,3*Math.PI]){
    const point=logarithmPoint(1,theta);
    assert.equal(point.principal,null);
    near(point.imag,theta);
  }
  near(logarithmPoint(1,Math.PI+0.01).principal,-Math.PI+0.01);
  near(logarithmPoint(1,Math.PI-0.01).principal,Math.PI-0.01);
});

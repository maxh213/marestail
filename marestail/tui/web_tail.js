(function(){
  function wire(id,key,cls){
    var btn=document.getElementById(id);
    if(!btn)return;
    function sync(){btn.setAttribute("aria-pressed",document.documentElement.classList.contains(cls)?"true":"false");}
    sync();
    btn.addEventListener("click",function(){
      var on=!document.documentElement.classList.contains(cls);
      document.documentElement.classList.toggle(cls,on);
      try{localStorage.setItem(key,on?"1":"0");}catch(e){}
      sync();
    });
  }
  wire("watch-switch","dash-watch-active-only","only-active-watch");
  wire("runs-switch","dash-runs-active-only","only-active-runs");
  wire("dandelion-switch","dash-dandelion-usage-only","only-usage-dandelion");
  var pop=document.getElementById("task-pop");
  var popTitle=document.getElementById("pop-title");
  var popBody=document.getElementById("pop-body");
  var returnTo=null;
  function section(title, text){
    var h=document.createElement("h3");
    h.textContent=title;
    var pre=document.createElement("pre");
    pre.textContent=text||"nothing written";
    popBody.append(h, pre);
  }
  function closePop(){
    pop.hidden=true;
    if(returnTo)returnTo.focus();
  }
  function openTask(btn){
    returnTo=btn;
    popTitle.textContent=btn.dataset.repo+" / "+btn.dataset.task;
    popBody.replaceChildren();
    section("reading", "…");
    pop.hidden=false;
    document.getElementById("pop-close").focus();
    var q=new URLSearchParams({repo:btn.dataset.repo, task:btn.dataset.task});
    fetch("/task?"+q).then(function(res){return res.json().then(function(data){return {ok:res.ok, data:data};});}).then(function(res){
      popBody.replaceChildren();
      if(!res.ok){section("error", res.data.error||"could not read that task");return;}
      popTitle.textContent=res.data.repo+" / "+res.data.task+(res.data.state?"  ·  "+res.data.state:"");
      section("── the task", res.data.task_text);
      section("── working on"+(res.data.step?"  ·  "+res.data.step:""), res.data.working);
    }).catch(function(){
      popBody.replaceChildren();
      section("error", "could not read that task");
    });
  }
  var runs=document.querySelector(".runs");
  if(runs)runs.addEventListener("click", function(e){
    var btn=e.target.closest(".task-link");
    if(btn)openTask(btn);
  });
  document.getElementById("pop-close").addEventListener("click", closePop);
  pop.addEventListener("click", function(e){if(e.target===pop)closePop();});
  document.addEventListener("keydown", function(e){if(e.key==="Escape"&&!pop.hidden)closePop();});
  setInterval(function(){if(pop.hidden)location.reload();}, 30000);
})();

try{
  if(localStorage.getItem("dash-watch-active-only")!=="0")document.documentElement.classList.add("only-active-watch");
  if(localStorage.getItem("dash-runs-active-only")!=="0")document.documentElement.classList.add("only-active-runs");
  if(localStorage.getItem("dash-dandelion-usage-only")!=="0")document.documentElement.classList.add("only-usage-dandelion");
}catch(e){}

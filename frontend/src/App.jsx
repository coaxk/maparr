import React, {useEffect, useState, useRef} from 'react'
import axios from 'axios'
import './App.css'
import './index.css'

function Spinner(){
  return <div className="spinner" role="status" aria-label="Loading"></div>
}

function ProgressBar({value=0}){
  return (
    <div className="progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} role="progressbar">
      <div className="bar" style={{width:`${value}%`}} />
    </div>
  )
}

function Tooltip({children, label}){
  return (
    <span className="tooltip" tabIndex={0} aria-label={label} title={label}>
      {children}
    </span>
  )
}

function Toast({message, onClose}){
  useEffect(()=>{
    const t=setTimeout(()=>onClose && onClose(), 3000)
    return ()=>clearTimeout(t)
  },[onClose])
  return (
    <div className="toast" role="status" aria-live="polite">{message}</div>
  )
}

function CopyCmd({cmd}){
  const [copied,setCopied]=useState(false)
  async function copy(){
    try{ await navigator.clipboard.writeText(cmd); setCopied(true); setTimeout(()=>setCopied(false),1200) }catch(e){}
  }
  return (
    <span style={{display:'inline-flex',alignItems:'center',gap:4}}>
      <code className="mono" style={{fontSize:'0.85rem',color:'var(--accent)'}}>{cmd}</code>
      <button className="cmd-copy" onClick={copy} aria-label={`Copy: ${cmd}`}>{copied?'Copied':'Copy'}</button>
    </span>
  )
}

function Modal({open, onClose, title, children}){
  if(!open) return null
  return (
    <div className="modal-overlay" onClick={(e)=>{if(e.target===e.currentTarget) onClose()}}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="card-header" style={{marginBottom:16}}>
          <div className="card-title">{title}</div>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Close">X</button>
        </div>
        {children}
      </div>
    </div>
  )
}

function detectClientOS(){
  const ua = navigator.userAgent.toLowerCase()
  const platform = navigator.platform?.toLowerCase() || ''
  if(ua.includes('win')) return 'windows'
  if(ua.includes('mac') || ua.includes('darwin')) return 'mac'
  if(ua.includes('linux')){
    // WSL2 browsers run on Windows but report Linux in some contexts
    if(ua.includes('wsl') || ua.includes('microsoft')) return 'wsl2'
    return 'linux'
  }
  if(platform.includes('win')) return 'windows'
  if(platform.includes('mac')) return 'mac'
  if(platform.includes('linux')) return 'linux'
  return 'unknown'
}

function DockerHelp({onRetry, onSkip}){
  const [retrying, setRetrying] = useState(false)
  const os = detectClientOS()

  async function handleRetry(){
    setRetrying(true)
    try{
      if(onRetry) await onRetry()
    }finally{
      setRetrying(false)
    }
  }

  const sections = [
    {
      id: 'desktop',
      show: os === 'windows' || os === 'mac' || os === 'unknown',
      title: 'Docker Desktop (Windows/Mac)',
      steps: [
        {text: 'Ensure the Docker Desktop app is running'},
        {text: 'Check: Icon in system tray should be active'},
        {text: 'If stuck: Restart Docker Desktop'},
      ],
    },
    {
      id: 'engine',
      show: os === 'linux' || os === 'unknown',
      title: 'Docker Engine (Linux)',
      steps: [
        {text: 'Start the Docker service:', cmd: 'sudo systemctl start docker'},
        {text: 'Check status:', cmd: 'docker --version'},
        {text: 'Verify running:', cmd: 'docker info'},
      ],
    },
    {
      id: 'wsl2',
      show: os === 'windows' || os === 'wsl2' || os === 'unknown',
      title: 'WSL2 (Windows with Docker Desktop)',
      steps: [
        {text: 'Docker Desktop must be running on Windows'},
        {text: 'WSL2 integration auto-detects it'},
        {text: 'Check inside WSL:', cmd: 'docker ps'},
      ],
    },
    {
      id: 'podman',
      show: true,
      title: 'Podman (alternative to Docker)',
      steps: [{text: 'Not yet supported (coming in v1.1)'}],
    },
  ]

  return (
    <section className="card" role="alert" aria-label="Docker connection help">
      <div className="card-header">
        <div>
          <div className="card-title">Docker Connection Failed</div>
          <div className="card-sub">MapArr needs Docker to detect your containers. Here's how to fix it based on your setup:</div>
        </div>
      </div>

      <div className="list" style={{marginTop:12}}>
        {sections.filter(s=>s.show).map(s=>(
          <div key={s.id} className="card" style={{padding:'12px 16px'}}>
            <div className="card-title" style={{fontSize:'0.95rem'}}>{s.title}</div>
            <ul style={{margin:'6px 0 0 0', paddingLeft:20, listStyle:'none'}}>
              {s.steps.map((step,i)=>(
                <li key={i} className="small" style={{marginTop:3}}>
                  {step.text}{step.cmd ? <>{' '}<CopyCmd cmd={step.cmd} /></> : null}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="row" style={{marginTop:16}}>
        <button className="btn btn-primary" onClick={handleRetry} disabled={retrying}>
          {retrying ? 'Retrying...' : 'Retry Connection'}
        </button>
        <button type="button" className="btn btn-outline" onClick={onSkip}>Skip & Continue Anyway</button>
      </div>

      <div className="small muted" style={{marginTop:12}}>
        Still stuck? See <a href="/TROUBLESHOOTING.md" target="_blank" rel="noopener noreferrer" style={{color:'var(--accent)'}}>TROUBLESHOOTING.md</a> for more help.
      </div>
    </section>
  )
}

function InfoIcon({label}){
  return (
    <Tooltip label={label}>
      <span style={{display:'inline-flex',alignItems:'center',justifyContent:'center',width:16,height:16,borderRadius:'50%',border:'1px solid var(--border)',fontSize:'0.65rem',fontWeight:700,color:'var(--muted)',cursor:'help',marginLeft:4,verticalAlign:'middle'}}>i</span>
    </Tooltip>
  )
}

function CodeBlock({code}){
  const [copied,setCopied]=useState(false)
  async function copy(){
    try{
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(()=>setCopied(false),1500)
    }catch(e){
      setCopied(false)
    }
  }
  return (
    <div className="code-block" aria-label="Code sample">
      <button className="btn btn-outline code-copy" onClick={copy} aria-label="Copy code">{copied? 'Copied' : 'Copy'}</button>
      <pre className="mono small" style={{margin:0,whiteSpace:'pre-wrap'}}>{code}</pre>
    </div>
  )
}

export default function App(){
  const [screen,setScreen]=useState('landing')
  const [path,setPath]=useState('')
  const [error,setError]=useState('')
  const [progress,setProgress]=useState(0)
  const [toast,setToast]=useState(null)
  const [analysis,setAnalysis]=useState(null)
  const [dockerStatus,setDockerStatus]=useState({connected:false,method:null,error:null})
  const [manualHint,setManualHint]=useState('')
  const headingRef = useRef(null)

  useEffect(()=>{ if(screen==='analysis' && headingRef.current) headingRef.current.focus() },[screen])

  // Check docker availability on mount and auto-start quick detect when connected
  useEffect(()=>{
    let mounted=true
    async function checkDocker(){
      try{
        const res = await axios.get('/api/docker/status')
        if(!mounted) return
        const connected = !!res?.data?.connected
        setDockerStatus({connected, method: res?.data?.method ?? null, error: res?.data?.error ?? null})
        if(!connected){
          setScreen('dockerError')
          setToast('Docker not available')
        }else{
          // Auto-start quick detect on landing when docker is available
          if(mounted){
            useDefaults()
            startDetect()
          }
        }
      }catch(err){
        if(!mounted) return
        setDockerStatus({connected:false, method:null, error: err?.message})
        setScreen('dockerError')
      }
    }
    checkDocker()
    return ()=>{ mounted=false }
  },[])

  // Normalize backend analysis object into the flat shape the UI renders
  function normalizeAnalysis(raw){
    if(!raw) return {summary:'No data', conflicts:0, recommendations:0, report:''}
    const s = raw.summary || {}
    const conflicts = Array.isArray(raw.conflicts) ? raw.conflicts : []
    const recs = Array.isArray(raw.recommendations) ? raw.recommendations : []
    const platform = raw.platform || s.platform_detected || 'unknown'
    const status = s.status || (conflicts.length ? 'needs_attention' : 'healthy')

    const reportLines = []
    reportLines.push(`Platform: ${platform}`)
    reportLines.push(`Containers analyzed: ${s.containers_analyzed ?? 0}`)
    reportLines.push(`Status: ${status}`)
    if(conflicts.length){
      reportLines.push(`\n--- Conflicts (${conflicts.length}) ---`)
      conflicts.forEach((c,i)=>{
        reportLines.push(`${i+1}. [${c.severity}] ${c.type}: ${c.note || c.destination || ''}`)
        if(c.fix) reportLines.push(`   Fix: ${c.fix.description || c.fix.action || ''}`)
      })
    }
    if(recs.length){
      reportLines.push(`\n--- Recommendations (${recs.length}) ---`)
      recs.forEach((r,i)=>{
        reportLines.push(`${i+1}. [${r.priority}] ${r.title}: ${r.description || ''}`)
        if(r.action) reportLines.push(`   Action: ${r.action}`)
      })
    }

    return {
      summary: `${platform} | ${s.containers_analyzed ?? 0} containers | ${status}`,
      conflicts: conflicts.length,
      recommendations: recs.length,
      report: reportLines.join('\n'),
      rawConflicts: conflicts,
      rawRecommendations: recs,
      platform,
      status,
    }
  }

  function validatePath(p){
    if(!p || p.trim().length<3){
      return 'Please enter a valid Docker setup path'
    }
    return ''
  }

  async function startDetect(e){
    e && e.preventDefault()
    const v=validatePath(path)
    if(v){ setError(v); return }
    setError('')
    setScreen('detecting')
    setProgress(8)

    try{
      const res = await axios.post('/api/analyze', {path})

      if(res?.data?.error){
        setError(res.data.error)
        setScreen('error')
        return
      }

      // Backend returns the analysis object directly at res.data
      // Normalize into the shape the UI expects
      const raw = res.data.analysis || res.data
      if(raw?.summary || raw?.conflicts || raw?.platform){
        setAnalysis(normalizeAnalysis(raw))
        setProgress(100)
        setTimeout(()=>{ setScreen('analysis'); setToast('Analysis complete') }, 300)
        try{ fetchRecommendations() }catch(e){}
        return
      }

      // Otherwise expect a jobId and subscribe to SSE for live progress
      const jobId = res?.data?.jobId
      if(!jobId){
        // fallback to recommendations
        const rec = await axios.get('/api/recommendations',{params:{path}})
        setAnalysis(normalizeAnalysis(rec?.data))
        setProgress(100)
        setScreen('analysis')
        setToast('Partial analysis complete')
        return
      }

      setProgress(12)
      const es = new EventSource(`/api/job/${jobId}/events`)
      es.onmessage = (ev) => {
        try{
          const data = JSON.parse(ev.data)
          if(data.progress !== undefined) setProgress(data.progress)
          if(data.status) {
            if(data.status === 'complete'){
              if(data.result) setAnalysis(normalizeAnalysis(data.result))
              setProgress(100)
              setToast('Analysis complete')
              setTimeout(()=>setScreen('analysis'), 200)
              es.close()
            }else if(data.status === 'error'){
              setError(data.error || 'Analysis error')
              setScreen('error')
              es.close()
            }
          }
        }catch(e){ console.error('sse parse', e) }
      }
      es.onerror = (err) => {
        // network or server closed the stream
        console.error('SSE error', err)
      }

    }catch(err){
      console.error(err)
      setError(err?.response?.data?.detail || err?.response?.data?.error || err.message || 'Analysis failed')
      setScreen('error')
    }
  }

  function useDefaults(){
    setPath('/workspace/docker-setup')
    setToast('Default Docker setup selected')
  }

  // simulateDockerError removed (no test button in production)

  function retry(){
    setScreen('landing')
    setProgress(0)
    setAnalysis(null)
    setManualHint('Still not working? Try manual setup below.')
    // Re-check docker status
    axios.get('/api/docker/status').then(res=>{
      const connected = !!res?.data?.connected
      setDockerStatus({connected, method: res?.data?.method ?? null, error: res?.data?.error ?? null})
      if(connected){ setToast('Docker reconnected') }
    }).catch(()=>{})
  }

  const [showApplyModal,setShowApplyModal]=useState(false)
  const [reviewMarked,setReviewMarked]=useState(false)

  function downloadReport(){
    if(!analysis?.report) return
    const blob = new Blob([`MapArr Analysis Report\nGenerated: ${new Date().toISOString()}\n\n${analysis.report}`], {type:'text/plain'})
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `maparr-report-${Date.now()}.txt`
    a.click()
    URL.revokeObjectURL(url)
    setToast('Report downloaded')
  }

  function exportReport(){
    if(!analysis?.report) return
    try{
      navigator.clipboard.writeText(analysis.report)
      setToast('Report copied to clipboard')
    }catch(e){
      downloadReport()
    }
  }

  const LEARN_MORE_LINKS = {
    'WSL2 Path Conversion': 'https://trash-guides.info/Hardlinks/How-to-setup-for/Docker/',
    'WSL2 Path Performance': 'https://learn.microsoft.com/en-us/windows/wsl/filesystems',
    'Use /mnt/user for Hardlinks': 'https://trash-guides.info/Hardlinks/How-to-setup-for/Unraid/',
    'Synology Volume Paths': 'https://trash-guides.info/Hardlinks/How-to-setup-for/Synology/',
    'Single Root Data Directory': 'https://trash-guides.info/Hardlinks/How-to-setup-for/Docker/',
    'Consistent UID/GID': 'https://trash-guides.info/Hardlinks/How-to-setup-for/Docker/',
    'Resolve Critical Conflicts': 'https://trash-guides.info/Hardlinks/Hardlinks-and-Instant-Moves/',
  }

  async function fetchRecommendations(){
    try{
      const res = await axios.get('/api/recommendations',{params:{path}})
      if(res?.data){
        const norm = normalizeAnalysis(res.data)
        setAnalysis(prev=>({...(prev||{}), recommendations: norm.recommendations, report: norm.report}))
        setToast('Recommendations loaded')
      }
    }catch(e){
      // non-fatal
    }
  }

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <div id="main-content" className="app-container" role="main" onKeyDown={(e)=>{/* reserved for future keyboard shortcuts */}}>
      {toast && <Toast message={toast} onClose={()=>setToast(null)} />}

      {screen==='landing' && (
        <section aria-labelledby="hero-title" className="hero card" role="region" aria-label="Landing">
          <div className="card-header">
            <div>
              <h1 id="hero-title">Welcome to MapArr</h1>
              <div className="card-sub">Analyze and reconcile Docker setup quickly.</div>
            </div>
            <div className="row">
              <div className="col" style={{alignItems:'flex-end'}}>
                <div className="small muted">Docker Engine: {dockerStatus.connected ? 'Connected' : 'Not connected'} {dockerStatus.method ? `(${dockerStatus.method})` : ''}</div>
              </div>
            </div>
          </div>

          <form onSubmit={startDetect} className="col" aria-label="Detection form">
            <label className="label" htmlFor="pathInput">Docker setup (manual) <InfoIcon label="Enter the host path to your Docker setup or mount"/></label>
            <input id="pathInput" className="input" value={path} onChange={(e)=>setPath(e.target.value)} placeholder="/path/to/docker-setup or C:\\docker-setup" aria-required="true" aria-invalid={!!error} />
            {error && <div role="alert" style={{color:'var(--danger)'}}>{error}</div>}

            <div className="row" style={{marginTop:12}}>
              <button type="submit" className="btn btn-primary">Start Analysis</button>
              <button type="button" className="btn btn-outline" onClick={useDefaults}>Use defaults</button>
            </div>
          </form>

          <div style={{marginTop:16}} className="muted small">Tips: Use the manual Docker setup for custom installations. Hover info for details.</div>
          {manualHint && <div style={{marginTop:12,color:'var(--muted)'}}>{manualHint}</div>}
        </section>
      )}

      {screen==='detecting' && (
        <section className="card" aria-live="polite" role="region" aria-label="Detecting"> 
          <div className="card-header">
            <div>
              <div className="card-title">Detecting environment…</div>
              <div className="card-sub">Probing Docker setup and containers</div>
            </div>
            <div className="row"><Spinner/></div>
          </div>

          <div style={{marginTop:8}}><ProgressBar value={progress} /></div>
          <div className="small muted" style={{marginTop:8}}>Progress: {progress}%</div>
        </section>
      )}

      {screen==='analysis' && analysis && (
        <section className="card" aria-labelledby="analysis-heading" role="region" aria-label="Analysis Summary">
          <div className="card-header">
            <div>
              <h2 id="analysis-heading" tabIndex={-1} ref={headingRef}>Analysis Summary</h2>
              <div className="card-sub">High level findings and recommended fixes</div>
            </div>
            <div className="row">
              <Tooltip label="Copy the full report text to your clipboard">
                <button className="btn btn-outline" onClick={exportReport}>Export</button>
              </Tooltip>
              <Tooltip label="Save the full report as a .txt file">
                <button className="btn btn-outline" onClick={downloadReport}>Download</button>
              </Tooltip>
              <button className="btn btn-ghost" onClick={retry}>Done</button>
            </div>
          </div>

          <div className="list">
            {/* Summary card */}
            <div className="card">
              <div className="card-title">Summary <InfoIcon label="Overall summary of detected issues and health"/></div>
              <div className="card-sub">{analysis.summary}</div>
              <div style={{marginTop:8}}>
                <span className={`badge ${analysis.status==='healthy'?'good':analysis.status==='needs_attention'?'warn':'bad'}`}>
                  {analysis.status==='healthy'?'Healthy':analysis.status==='needs_attention'?'Needs Attention':'Critical'}
                </span>
              </div>
            </div>

            {/* Conflicts card */}
            <div className="card">
              <div className="card-title">Conflicts <InfoIcon label="Containers or mounts that map different host paths to the same destination"/></div>
              <div className="card-sub">{analysis.conflicts} potential conflict{analysis.conflicts!==1?'s':''} detected</div>
              {analysis.rawConflicts?.length > 0 && (
                <ul style={{margin:'8px 0 0',paddingLeft:18,listStyle:'disc'}}>
                  {analysis.rawConflicts.slice(0,5).map((c,i)=>(
                    <li key={i} className="small" style={{marginTop:4,color:c.severity==='critical'?'var(--danger)':c.severity==='warning'?'#ffd98f':'var(--muted)'}}>
                      <strong>{c.type}</strong>: {c.note || c.destination || 'Unknown'}
                      {c.fix?.description && <span className="muted"> — {c.fix.description}</span>}
                    </li>
                  ))}
                  {analysis.rawConflicts.length > 5 && <li className="small muted">…and {analysis.rawConflicts.length - 5} more</li>}
                </ul>
              )}
              <div style={{marginTop:10}} className="row">
                <button className="btn btn-danger" onClick={()=>setReviewMarked(prev=>!prev)}>
                  {reviewMarked ? 'Unmark Review' : 'Mark for Review'}
                </button>
              </div>
              {reviewMarked && (
                <div className="small" style={{marginTop:8,padding:'8px 10px',background:'rgba(255,107,107,0.06)',borderRadius:8,border:'1px solid rgba(255,107,107,0.1)',color:'#ffadad'}}>
                  This analysis has been flagged for manual review. Check your <code>docker-compose.yml</code> volume mounts for the conflicts listed above before running your containers.
                </div>
              )}
            </div>

            {/* Recommendations card */}
            <div className="card">
              <div className="card-title">Recommendations <InfoIcon label="Suggested fixes and best practices for your setup"/></div>
              <div className="card-sub">{analysis.recommendations} recommended fix{analysis.recommendations!==1?'es':''}</div>
              {analysis.rawRecommendations?.length > 0 && (
                <ul style={{margin:'8px 0 0',paddingLeft:18,listStyle:'disc'}}>
                  {analysis.rawRecommendations.map((r,i)=>(
                    <li key={i} className="small" style={{marginTop:4}}>
                      <span className={`badge ${r.priority==='high'?'bad':r.priority==='medium'?'warn':'good'}`} style={{fontSize:'0.7rem',padding:'2px 6px',marginRight:6}}>{r.priority}</span>
                      <strong>{r.title}</strong>{r.description ? `: ${r.description}` : ''}
                      {r.action && <div className="muted" style={{marginTop:2,marginLeft:2}}>Action: {r.action}</div>}
                      {(() => {
                        const link = LEARN_MORE_LINKS[r.title]
                        return link ? <a href={link} target="_blank" rel="noopener noreferrer" style={{color:'var(--accent)',fontSize:'0.8rem',marginLeft:4}}>Learn more</a> : null
                      })()}
                    </li>
                  ))}
                </ul>
              )}
              {analysis.rawRecommendations?.length === 0 && (
                <div className="small muted" style={{marginTop:8}}>No recommendations — your setup looks good!</div>
              )}
            </div>

            {/* Actions card */}
            <div className="card">
              <div className="card-title">Actions</div>
              <div className="card-sub small muted">Apply suggested fixes or save the report for later</div>
              <div style={{marginTop:10}} className="row">
                <Tooltip label="View each conflict and suggested docker-compose fix">
                  <button className="btn btn-primary" onClick={()=>setShowApplyModal(true)} disabled={!analysis.rawConflicts?.length}>Apply Fixes</button>
                </Tooltip>
                <Tooltip label="Copy report to clipboard">
                  <button className="btn btn-outline" onClick={exportReport}>Copy Report</button>
                </Tooltip>
                <Tooltip label="Download as .txt file">
                  <button className="btn btn-outline" onClick={downloadReport}>Download</button>
                </Tooltip>
              </div>
            </div>
          </div>

          {/* Full report expandable */}
          <details style={{marginTop:12}}>
            <summary className="small muted" style={{cursor:'pointer'}}>View full report text</summary>
            <div style={{marginTop:8}}>
              <CodeBlock code={analysis.report} />
            </div>
          </details>
        </section>
      )}

      {/* Apply Fixes Modal */}
      <Modal open={showApplyModal} onClose={()=>setShowApplyModal(false)} title="Apply Fixes — Review Changes">
        {analysis?.rawConflicts?.length > 0 ? (
          <div className="col" style={{gap:12}}>
            <div className="small muted">Review each conflict and its suggested fix. Copy the compose snippets to update your <code>docker-compose.yml</code>.</div>
            {analysis.rawConflicts.map((c,i)=>(
              <div key={i} className="card" style={{padding:12}}>
                <div style={{display:'flex',alignItems:'center',gap:8}}>
                  <span className={`badge ${c.severity==='critical'?'bad':c.severity==='warning'?'warn':'good'}`} style={{fontSize:'0.7rem',padding:'2px 6px'}}>{c.severity}</span>
                  <strong className="small">{c.type}</strong>
                </div>
                <div className="small muted" style={{marginTop:4}}>{c.note || c.destination || ''}</div>
                {c.containers && <div className="small muted" style={{marginTop:2}}>Containers: {Array.isArray(c.containers) ? c.containers.join(', ') : String(c.containers)}</div>}
                {c.fix && (
                  <div style={{marginTop:8,padding:'8px 10px',background:'rgba(94,234,212,0.04)',borderRadius:8,border:'1px solid rgba(94,234,212,0.08)'}}>
                    <div className="small" style={{color:'var(--accent)',fontWeight:600}}>Suggested fix</div>
                    <div className="small" style={{marginTop:2}}>{c.fix.description}</div>
                    {c.fix.action && (
                      <div style={{marginTop:6}}>
                        <CopyCmd cmd={c.fix.action} />
                      </div>
                    )}
                    {c.fix.suggested_source && (
                      <div className="small muted" style={{marginTop:4}}>
                        Use source: <code style={{color:'var(--accent)'}}>{c.fix.suggested_source}</code>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            <div className="small muted" style={{marginTop:4}}>After updating your compose file, run: <CopyCmd cmd="docker-compose up -d" /></div>
          </div>
        ) : (
          <div className="small muted">No conflicts to fix — your setup is healthy!</div>
        )}
      </Modal>

      {screen==='dockerError' && (
        <DockerHelp
          onRetry={async ()=>{
            try{
              const res = await axios.post('/api/docker/reconnect')
              if(res?.data?.connected){
                setToast('Docker connected!')
                setScreen('landing')
              } else {
                setToast('Still not connected')
              }
            }catch(e){
              setToast('Retry failed')
            }
          }}
          onSkip={()=>{ setScreen('landing'); setToast('Continuing without Docker') }}
        />
      )}

      {screen==='error' && (
        <section className="card" role="alert" aria-label="Analysis failed"> 
          <div className="card-title">Analysis failed</div>
          <div className="card-sub">{error || 'Unexpected error'}</div>
          <div style={{marginTop:12}} className="row"><button className="btn btn-primary" onClick={retry}>Try again</button></div>
        </section>
      )}
      </div>
    </>
  )
}

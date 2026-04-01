// API Configuration - Hardcoded to exact localhost ports to fix networking errors
        const NODE_API_BASE = 'https://twb-backend.onrender.com';
        const PYTHON_API_BASE = 'https://twb-python-engine.onrender.com';
            
        const ENDPOINTS = {
            interest: `${NODE_API_BASE}/api/waitlist`, // Corrected to hit the likely /api/waitlist route
            analyze: `${NODE_API_BASE}/api/analyze`
        };

        // Sticky Navbar
        const navbar = document.getElementById('navbar');
        window.addEventListener('scroll', () => {
            if (window.scrollY > 20) {
                navbar.classList.add('scrolled');
            } else {
                navbar.classList.remove('scrolled');
            }
        });

        // --- UPLOAD & ANALYZE LOGIC ---
        const viewUpload = document.getElementById('view-upload');
        const viewLoading = document.getElementById('view-loading');
        const viewResults = document.getElementById('view-results');
        
        const dropzone = document.getElementById('dropzone');
        const fileInput = document.getElementById('file-input');
        const uploadError = document.getElementById('upload-error');

        function switchView(view) {
            viewUpload.classList.add('hidden');
            viewLoading.classList.add('hidden');
            viewLoading.style.display = 'none';
            viewResults.classList.add('hidden');
            viewResults.classList.remove('opacity-100', 'translate-y-0');

            if (view === 'upload') {
                viewUpload.classList.remove('hidden');
                fileInput.value = '';
            } else if (view === 'loading') {
                viewLoading.classList.remove('hidden');
                viewLoading.style.display = 'flex';
            } else if (view === 'results') {
                viewResults.classList.remove('hidden');
                void viewResults.offsetWidth;
                viewResults.classList.add('opacity-100', 'translate-y-0');
            }
        }

        function showError(msg) {
            uploadError.textContent = msg;
            uploadError.classList.remove('hidden');
            switchView('upload');
        }

        function updateResultsUI(data) {
            // Strictly enforce two decimal places for all currency values
            const formatCurrency = (val) => {
                const num = Number(val) || 0;
                return num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            };
            
            // Extract directly from Python FastAPI Response
            const summary = data.summary || {};
            const logicResults = data.logic_results || [];
            const health = data.health || {};
            const topMistakes = data.top_mistakes || [];
            
            const score = health.score || 0;
            const totalImpact = data.total_estimated_impact_inr || 0;

            // Find L2 logic which holds most fundamental execution metrics in Python
            const l2Logic = logicResults.find(r => r.logic_id === 'L2') || {};
            const l2Metrics = l2Logic.metrics || {};
            const l2Evidence = l2Logic.evidence || {};

            // 1. SAFELY Update High-Level Summary Cards
            const grossPnlValue = summary.realized_pnl || 0;
            const chargesValue = summary.charges_total || 0;
            const netPnlValue = summary.net_pnl || 0;
            
            const grossPnlEl = document.querySelector('[data-gross-pnl]');
            if (grossPnlEl) {
                grossPnlEl.textContent = `₹${formatCurrency(grossPnlValue)}`;
                grossPnlEl.className = `text-[clamp(1rem,3.5vw,1.25rem)] font-bold tracking-tight break-words ${grossPnlValue >= 0 ? 'text-accent' : 'text-danger'}`;
            }

            const chargesEl = document.querySelector('[data-charges]');
            if (chargesEl) chargesEl.textContent = `₹${formatCurrency(chargesValue)}`;

            const netPnlEl = document.querySelector('[data-net-pnl]');
            if (netPnlEl) {
                netPnlEl.textContent = `₹${formatCurrency(netPnlValue)}`;
                netPnlEl.className = `text-[clamp(1.1rem,4vw,1.5rem)] font-black tracking-tight relative z-10 break-words ${netPnlValue >= 0 ? 'text-accent drop-shadow-[0_0_15px_rgba(16,185,129,0.3)]' : 'text-danger drop-shadow-[0_0_15px_rgba(239,68,68,0.3)]'}`;
            }

            // 2. Execution Breakdown Stats
            const total = l2Metrics.total_trades || 0;
            const wins = l2Metrics.winners || 0;
            const losses = l2Metrics.losers || 0;
            const breakeven = l2Metrics.breakeven_trades || 0;
            
            const totalWinPnl = l2Metrics.gross_wins_inr || 0;
            const totalLossPnl = l2Metrics.gross_losses_inr || 0;
            const avgWin = l2Metrics.avg_winner_inr || 0;
            const avgLoss = l2Metrics.avg_loser_inr || 0;
            const rrRatio = l2Metrics.rr_ratio || 0;

            let largestWin = 0;
            let profitDependency = 0;
            
            if (l2Evidence.top_winners && l2Evidence.top_winners.length > 0) {
                largestWin = l2Evidence.top_winners[0].realized_pnl;
                const top3Profit = l2Evidence.top_winners.slice(0, 3).reduce((acc, t) => acc + t.realized_pnl, 0);
                profitDependency = totalWinPnl > 0 ? Math.round((top3Profit / totalWinPnl) * 100) : 0;
            }

            let largestLoss = 0;
            if (l2Evidence.top_losers && l2Evidence.top_losers.length > 0) {
                largestLoss = l2Evidence.top_losers[0].realized_pnl;
            }

            // SAFELY Update Dependency Card
            const depCard = document.getElementById('dependency-card');
            const depEl = document.querySelector('[data-profit-dependency]');
            if (depCard && depEl) {
                depEl.textContent = `${profitDependency}%`;
                if (profitDependency > 60) {
                    depEl.classList.remove('text-white');
                    depEl.classList.add('text-danger');
                    depCard.classList.remove('border-border');
                    depCard.classList.add('border-danger/30', 'bg-danger/5');
                } else {
                    depEl.classList.add('text-white');
                    depEl.classList.remove('text-danger');
                    depCard.classList.add('border-border');
                    depCard.classList.remove('border-danger/30', 'bg-danger/5');
                }
            }

            // SAFELY Render Progress Bars & Lower Cards
            const breakdownContainer = document.getElementById('res-trade-breakdown');
            if (breakdownContainer) {
                const winPct = total > 0 ? (wins / total) * 100 : 0;
                const lossPct = total > 0 ? (losses / total) * 100 : 0;
                const breakPct = total > 0 ? (breakeven / total) * 100 : 0;

                breakdownContainer.innerHTML = `
                    <h4 class="text-[10px] sm:text-xs font-bold text-text-secondary uppercase tracking-wider mb-5 text-center">Execution Breakdown</h4>
                    
                    <div class="space-y-4">
                        <div class="flex items-center justify-between border-b border-border/50 pb-3">
                            <span class="text-xs font-bold text-text-secondary uppercase tracking-wider">Total Executions</span>
                            <span class="text-sm font-black text-white">${total}</span>
                        </div>

                        <div>
                            <div class="flex justify-between items-end mb-1.5">
                                <span class="text-xs font-bold text-accent uppercase tracking-wider">Winning Trades</span>
                                <span class="text-sm font-black text-white">${wins} <span class="text-[10px] text-text-secondary font-medium ml-1">(${winPct.toFixed(0)}%)</span></span>
                            </div>
                            <div class="w-full bg-white/5 rounded-full h-2 overflow-hidden">
                                <div class="bg-accent h-full rounded-full transition-all duration-1000" style="width: ${winPct}%"></div>
                            </div>
                        </div>

                        <div>
                            <div class="flex justify-between items-end mb-1.5">
                                <span class="text-xs font-bold text-danger uppercase tracking-wider">Losing Trades</span>
                                <span class="text-sm font-black text-white">${losses} <span class="text-[10px] text-text-secondary font-medium ml-1">(${lossPct.toFixed(0)}%)</span></span>
                            </div>
                            <div class="w-full bg-white/5 rounded-full h-2 overflow-hidden">
                                <div class="bg-danger h-full rounded-full transition-all duration-1000" style="width: ${lossPct}%"></div>
                            </div>
                        </div>

                        <div>
                            <div class="flex justify-between items-end mb-1.5">
                                <span class="text-xs font-bold text-text-secondary uppercase tracking-wider">Breakeven / Scratch</span>
                                <span class="text-sm font-black text-white">${breakeven} <span class="text-[10px] text-text-secondary font-medium ml-1">(${breakPct.toFixed(0)}%)</span></span>
                            </div>
                            <div class="w-full bg-white/5 rounded-full h-2 overflow-hidden">
                                <div class="bg-text-secondary h-full rounded-full transition-all duration-1000" style="width: ${breakPct}%"></div>
                            </div>
                        </div>
                    </div>

                    <!-- The Good vs The Bad Metrics -->
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-6 pt-5 border-t border-border/50">
                        <div class="bg-accent/5 border border-accent/20 rounded-xl p-4">
                            <span class="block text-[10px] text-accent font-bold uppercase tracking-widest mb-3 text-center">The Good</span>
                            <div class="space-y-3">
                                <div class="flex justify-between items-center"><span class="text-xs text-text-secondary">Avg Win</span><span class="text-sm font-bold text-white">₹${formatCurrency(avgWin)}</span></div>
                                <div class="flex justify-between items-center"><span class="text-xs text-text-secondary">Highest Win</span><span class="text-sm font-bold text-white">₹${formatCurrency(largestWin)}</span></div>
                                <div class="flex justify-between items-center pt-2 border-t border-white/5"><span class="text-xs text-text-secondary">Total Gross Profit</span><span class="text-sm font-black text-accent">₹${formatCurrency(totalWinPnl)}</span></div>
                            </div>
                        </div>
                        <div class="bg-danger/5 border border-danger/20 rounded-xl p-4">
                            <span class="block text-[10px] text-danger font-bold uppercase tracking-widest mb-3 text-center">The Bad</span>
                            <div class="space-y-3">
                                <div class="flex justify-between items-center"><span class="text-xs text-text-secondary">Avg Loss</span><span class="text-sm font-bold text-white">₹${formatCurrency(Math.abs(avgLoss))}</span></div>
                                <div class="flex justify-between items-center"><span class="text-xs text-text-secondary">Highest Loss</span><span class="text-sm font-bold text-white">₹${formatCurrency(Math.abs(largestLoss))}</span></div>
                                <div class="flex justify-between items-center pt-2 border-t border-white/5"><span class="text-xs text-text-secondary">Total Gross Loss</span><span class="text-sm font-black text-danger">₹${formatCurrency(Math.abs(totalLossPnl))}</span></div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- RR Ratio -->
                    <div class="mt-4 bg-white/5 border border-border/50 rounded-xl p-4 flex justify-between items-center">
                        <div>
                            <span class="block text-[10px] text-text-secondary uppercase font-bold tracking-wider mb-0.5">Reward-to-Risk Ratio</span>
                            <span class="text-xs text-text-secondary/70">Average Win ÷ Average Loss</span>
                        </div>
                        <span class="text-xl font-black ${rrRatio >= 1.5 ? 'text-accent' : rrRatio >= 1 ? 'text-warning' : 'text-danger'}">${rrRatio > 999 ? '∞' : rrRatio.toFixed(2)}x</span>
                    </div>
                `;
                breakdownContainer.classList.remove('hidden');
            }

            // 3. SAFELY Update Reality Block 
            const realityBlockText = document.getElementById('res-reality-block');
            if (realityBlockText) {
                if (grossPnlValue >= 0) {
                    if (netPnlValue < 0) {
                        realityBlockText.innerHTML = `You made <span class="text-white">₹${formatCurrency(grossPnlValue)}</span><br>But charges turned it into a <span class="text-danger">₹${formatCurrency(Math.abs(netPnlValue))}</span> loss.`;
                    } else {
                        const keptPrefix = (netPnlValue > grossPnlValue * 0.7) ? "And kept" : "But kept only";
                        realityBlockText.innerHTML = `You made <span class="text-white">₹${formatCurrency(grossPnlValue)}</span><br>${keptPrefix} <span class="text-accent">₹${formatCurrency(netPnlValue)}</span> after charges.`;
                    }
                } else {
                    realityBlockText.innerHTML = `You lost <span class="text-danger">₹${formatCurrency(Math.abs(grossPnlValue))}</span><br>And it worsened to <span class="text-danger">₹${formatCurrency(Math.abs(netPnlValue))}</span> after charges.`;
                }
            }
            
            // SAFELY Update Dynamic Summary Message
            const summaryMsgEl = document.getElementById('res-summary-message');
            const chargeImpact = grossPnlValue > 0 ? chargesValue / grossPnlValue : 0;
            let message = "";
            
            if (summaryMsgEl) {
                if (netPnlValue < 0) {
                    message = "You are losing money after costs. Your current approach is not working.";
                    summaryMsgEl.className = "text-[11px] sm:text-xs font-bold text-danger uppercase tracking-wider";
                } else if (netPnlValue > 0 && chargeImpact > 0.3) {
                    message = "You are profitable, but a large portion is lost to charges. This is slowing your growth.";
                    summaryMsgEl.className = "text-[11px] sm:text-xs font-bold text-warning uppercase tracking-wider";
                } else if (netPnlValue > 0 && chargeImpact <= 0.3) {
                    message = "You are profitable. Your current approach is working.";
                    summaryMsgEl.className = "text-[11px] sm:text-xs font-bold text-accent uppercase tracking-wider";
                }
                
                if (netPnlValue > grossPnlValue * 0.7) {
                    message = "You are managing costs efficiently and keeping most of your profits.";
                    summaryMsgEl.className = "text-[11px] sm:text-xs font-bold text-accent uppercase tracking-wider";
                }
                summaryMsgEl.textContent = message;
            }

            // SAFELY Update Dynamic CTA Transition and Title for CLIFFHANGER
            const cta1 = document.getElementById('cta-transition-1');
            const cta2 = document.getElementById('cta-transition-2');
            const ctaCardTitle = document.getElementById('cta-card-title');
            
            if (cta1 && cta2 && ctaCardTitle) {
                // Unified CTA to push unlocking the full report
                cta1.textContent = "We found the exact behavioral patterns affecting your P&L.";
                cta2.textContent = "Join the early access and unlock your full report.";
                ctaCardTitle.textContent = "Unlock Your Complete Behavioral Analysis";
            }

            // 4. SAFELY Render Primary Insight
            const insights = [];
            
            if (score >= 85) {
                insights.push({
                    title: "Excellent Execution. Your approach is working.",
                    desc: "Maintain your discipline. Focus on scaling your edge without increasing risk proportionally."
                });
            }

            topMistakes.slice(0, 2).forEach(m => {
                insights.push({
                    title: m.headline,
                    desc: m.recommendation
                });
            });

            if (insights.length === 0) {
                insights.push({
                    title: "Stable Execution Profile",
                    desc: "No critical behavioral or algorithmic mistakes were detected in this trading period."
                });
            }

            const primaryTitleEl = document.getElementById('res-primary-title');
            const primaryDescEl = document.getElementById('res-primary-desc');
            if (primaryTitleEl && primaryDescEl && insights.length > 0) {
                const primary = insights[0];
                primaryTitleEl.textContent = primary.title;
                primaryDescEl.textContent = primary.desc;
            }

            switchView('results');
        }

        async function handleFileUpload(file) {
            uploadError.classList.add('hidden');
            if (!file) return;
            
            if (!file.name.toLowerCase().endsWith('.xlsx') && !file.name.toLowerCase().endsWith('.xls') && file.type !== 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet') {
                showError("Invalid format. Please upload a Zerodha P&L Excel file (.xlsx or .xls).");
                return;
            }

            const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB limit for python backend
            if (file.size > MAX_FILE_SIZE) {
                showError("File is too large. Please upload an Excel file smaller than 10MB.");
                return;
            }

            switchView('loading');

            const formData = new FormData();
            formData.append('file', file, file.name);

            try {
                const res = await fetch(ENDPOINTS.analyze, {
                    method: 'POST',
                    body: formData
                });
                
                if (!res.ok) {
                    const errData = await res.json().catch(() => ({}));
                    showError(errData.detail || errData.message || "Could not analyze this file. Make sure it is a full Zerodha P&L export.");
                    return;
                }

                const data = await res.json();
                updateResultsUI(data);
                attachModalTriggers();

            } catch (err) {
                console.error("Fetch error: ", err);
                showError("Failed to connect to the analysis engine. Is your Python server running on port 8000?");
            }
        }

        // Dropzone Events
        dropzone.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', (e) => handleFileUpload(e.target.files[0]));
        
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
            }, false);
        });

        ['dragenter', 'dragover'].forEach(eventName => {
            dropzone.addEventListener(eventName, () => dropzone.classList.add('drag-active'), false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropzone.addEventListener(eventName, () => dropzone.classList.remove('drag-active'), false);
        });

        dropzone.addEventListener('drop', (e) => {
            handleFileUpload(e.dataTransfer.files[0]);
        });

        // --- WAITLIST MODAL LOGIC ---
        const modal = document.getElementById('waitlist-modal');
        const modalClose = document.getElementById('modal-close');
        const waitlistForm = document.getElementById('waitlist-form');
        const formMessage = document.getElementById('form-message');
        const submitBtn = document.getElementById('submit-btn');
        
        const attachModalTriggers = () => {
            const modalTriggers = document.querySelectorAll('.modal-trigger');
            modalTriggers.forEach(trigger => {
                const newTrigger = trigger.cloneNode(true);
                trigger.parentNode.replaceChild(newTrigger, trigger);
                
                newTrigger.addEventListener('click', (e) => {
                    e.preventDefault();
                    modal.classList.remove('invisible');
                    setTimeout(() => modal.classList.add('active'), 10);
                    document.body.style.overflow = 'hidden';
                });
            });
        };
        attachModalTriggers();

        const closeModal = () => {
            modal.classList.remove('active');
            setTimeout(() => {
                modal.classList.add('invisible');
                document.body.style.overflow = '';
                formMessage.className = 'hidden';
                if(!formMessage.classList.contains('text-accent')) {
                    submitBtn.disabled = false;
                    submitBtn.style.opacity = '1';
                    submitBtn.textContent = 'Request Early Access';
                }
            }, 200);
        };

        modalClose.addEventListener('click', closeModal);
        modal.addEventListener('click', (e) => {
            if(e.target === modal) closeModal();
        });

        waitlistForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const fullName = document.getElementById('fullName').value.trim();
            const email = document.getElementById('email').value.trim();
            const tradingType = document.getElementById('tradingType').value;
            const challenge = document.getElementById('challenge').value.trim();

            if (!fullName || !email || !tradingType) return;

            submitBtn.disabled = true;
            submitBtn.textContent = 'Joining...';
            submitBtn.style.opacity = '0.7';
            formMessage.className = 'hidden';

            try {
                const response = await fetch(ENDPOINTS.interest, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ fullName, email, tradingType, challenge })
                });

                // Attempt to extract JSON from response to surface specific server errors
                let responseData = {};
                try {
                    responseData = await response.json();
                } catch (err) {}

                if (response.ok) {
                    formMessage.textContent = "You're on the list. Check your email.";
                    formMessage.className = 'block p-2.5 rounded-lg mb-4 text-xs font-bold text-center bg-accent/10 text-accent border border-accent/20';
                    waitlistForm.reset();
                    submitBtn.textContent = "You're in 🚀";
                    submitBtn.disabled = true;
                    submitBtn.style.opacity = '0.7';
                } else if (response.status === 409) {
                    formMessage.textContent = responseData.message || "You are already on the early access list.";
                    formMessage.className = 'block p-2.5 rounded-lg mb-4 text-xs font-bold text-center bg-danger/10 text-danger border border-danger/20';
                } else if (response.status === 404) {
                    // Explicitly catch the 404 to help the user debug their local setup
                    throw new Error("API Route not found (404). Ensure 'server.js' is currently running on Port 5000 (Check that you didn't run tradebook.js or a Python file on Port 5000 by mistake).");
                } else {
                    // Throw the exact backend message so the catch block displays it
                    const errorMsg = responseData.error?.message || responseData.error || responseData.message || `Server Error (${response.status})`;
                    throw new Error(errorMsg);
                }
            } catch (error) {
                console.error("Submission error:", error);
                
                const errMsg = error.message || "";
                
                // Explicitly intercept Network Errors to give actionable advice
                if (errMsg.includes('Failed to fetch') || errMsg.includes('NetworkError')) {
                    formMessage.textContent = "Cannot connect to backend. Is Node server.js running on port 5000?";
                } else if (errMsg.includes('duplicate key') || errMsg.includes('waitlist_email_key')) {
                    formMessage.textContent = "You are already on the early access list.";
                } else {
                    // Display the true error from the server instead of generic "Something went wrong"
                    formMessage.textContent = errMsg || "Something went wrong. Try again.";
                }
                
                formMessage.className = 'block p-2.5 rounded-lg mb-4 text-xs font-bold text-center bg-danger/10 text-danger border border-danger/20';
            } finally {
                if(!formMessage.classList.contains('text-accent')) {
                    submitBtn.disabled = false;
                    submitBtn.style.opacity = '1';
                    submitBtn.textContent = 'Request Early Access';
                }
            }
        });
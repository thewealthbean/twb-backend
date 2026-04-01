// server.js - TradeProof Backend
require('dotenv').config();
const express = require('express');
const cors = require('cors');
const { createClient } = require('@supabase/supabase-js');
const { Resend } = require('resend');

const app = express();
const PORT = process.env.PORT || 5000;

const path = require('path');

// serve frontend folder
app.use(express.static(path.join(__dirname, '../frontend')));

// default route
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, '../frontend/index.html'));
});

// Middleware
app.use(cors());
app.use(express.json());

// --- Supabase Setup ---
const supabaseUrl = process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_SECRET_KEY; // Service Role Key
if (!supabaseUrl || !supabaseKey) {
    console.error("Supabase URL or Secret Key missing in .env");
    process.exit(1);
}
const supabase = createClient(supabaseUrl, supabaseKey);

// --- Resend Setup ---
const resend = new Resend(process.env.RESEND_API_KEY);
if (!process.env.RESEND_API_KEY) {
    console.warn("Resend API key missing, emails will fail.");
}

// --- STATS ENDPOINT ---
app.get('/api/stats', async (req, res) => {
    try {
        const { count, error: countError } = await supabase
            .from('waitlist')
            .select('*', { count: 'exact', head: true });
        if (countError) throw countError;

        const { data: recentLeads, error: leadsError } = await supabase
            .from('waitlist')
            .select('full_name')
            .order('created_at', { ascending: false })
            .limit(4);
        if (leadsError) throw leadsError;

        const initials = recentLeads.map(lead => lead.full_name ? lead.full_name.charAt(0).toUpperCase() : '?');

        res.json({
            success: true,
            count: count || 0,
            recentInitials: initials
        });

    } catch (error) {
        console.error('Error fetching stats:', error);
        res.status(500).json({ success: false, message: 'Failed to fetch stats.' });
    }
});

// --- SUBMIT FORM / ADD LEAD ---
app.post('/api/waitlist', async (req, res) => {
    const { fullName, email, tradingType, challenge } = req.body;
    console.log('Received payload:', req.body);

    if (!fullName || !email || !tradingType) {
        return res.status(400).json({ success: false, message: 'Missing required fields.' });
    }

    try {
        // 1️⃣ Insert into Supabase
        const { error: insertError } = await supabase
            .from('waitlist')
            .insert([{ full_name: fullName, email, trading_type: tradingType, challenge }]);

        if (insertError) {
            console.error('Supabase insert error:', insertError);
            if (insertError.code === '23505') {
                return res.status(409).json({ success: false, message: 'You are already on the early access list!' });
            }
            throw insertError;
        }

        // 2️⃣ Send Welcome Email via Resend
        const firstName = fullName.split(' ')[0];
        try {
            await resend.emails.send({
            from: 'the wealth bean <onboarding@thewealthbean.com>', // verified custom domain
            to: email,
            subject: 'Welcome to the wealth bean Early Access 🚀',
          html: `
            <div style="font-family: Arial, sans-serif; color: #111; line-height: 1.6;">
                <h2 style="color: #0ea5e9; font-size: 24px; margin-bottom: 12px;">🚀 Welcome to the wealth bean, ${firstName}!</h2>
                
                <p style="margin-bottom: 16px;">
                You’re officially on the early access list for the ultimate verified trading journal and community for Indian traders. We’re thrilled to have you with us! 🎉
                </p>

                <p style="margin-bottom: 16px; font-weight: bold; color: #10b981;">
                As a special thank you, you’ve secured <strong>1 month of the wealth bean Pro for free</strong> when we launch. 💎
                </p>

                <p style="margin-bottom: 16px;">
                Get ready for exciting updates, exclusive features, and a vibrant trading community that’s all about verified results and growth. 📈
                </p>

                <p style="margin-bottom: 24px; color: #6b7280;">
                Keep an eye on your inbox — big things are coming soon!
                </p>

                <p style="font-weight: bold;">— The Wealth Bean Team</p>

                <hr style="margin: 20px 0; border: none; border-top: 1px solid #e5e7eb;" />

                <p style="font-size: 12px; color: #9ca3af;">
                You are receiving this email because you joined the TWB early access list. If this wasn’t you, please ignore this email.
                </p>
            </div>
`
});
        } catch (emailError) {
            console.error('Email sending failed (but user was saved):', emailError);
        }

        res.status(201).json({ success: true, message: 'Joined successfully!' });

    } catch (error) {
        console.error('Server error:', error);
        res.status(500).json({ success: false, message: 'Internal server error.' });
    }
});

// --- START SERVER ---
app.listen(PORT, () => {
    console.log(`Backend server running on http://localhost:${PORT}`);
});
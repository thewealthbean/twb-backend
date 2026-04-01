// server.js - TradeProof Backend
require('dotenv').config();

const express = require('express');
const cors = require('cors');
const axios = require('axios');
const multer = require('multer');
const fs = require('fs');
const FormData = require('form-data');
const path = require('path');

const { createClient } = require('@supabase/supabase-js');
const { Resend } = require('resend');

const app = express();
const PORT = process.env.PORT || 5000;

// ================================
// SERVE FRONTEND
// ================================
app.use(express.static(path.join(__dirname, '../frontend')));

app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, '../frontend/index.html'));
});

// ================================
// MIDDLEWARE
// ================================
app.use(cors());
app.use(express.json());

// Ensure uploads folder exists
if (!fs.existsSync('uploads')) {
  fs.mkdirSync('uploads');
}

const upload = multer({ dest: 'uploads/' });

// ================================
// SUPABASE SETUP
// ================================
const supabaseUrl = process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_SECRET_KEY;

if (!supabaseUrl || !supabaseKey) {
  console.error("Supabase URL or Secret Key missing in .env");
  process.exit(1);
}

const supabase = createClient(supabaseUrl, supabaseKey);

// ================================
// RESEND SETUP
// ================================
const resend = new Resend(process.env.RESEND_API_KEY);

if (!process.env.RESEND_API_KEY) {
  console.warn("Resend API key missing, emails will fail.");
}

// ================================
// STATS ENDPOINT
// ================================
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

    const initials = recentLeads.map(lead =>
      lead.full_name ? lead.full_name.charAt(0).toUpperCase() : '?'
    );

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

// ================================
// WAITLIST ENDPOINT
// ================================
app.post('/api/waitlist', async (req, res) => {
  const { fullName, email, tradingType, challenge } = req.body;

  if (!fullName || !email || !tradingType) {
    return res.status(400).json({ success: false, message: 'Missing required fields.' });
  }

  try {
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

    // Send Email
    const firstName = fullName.split(' ')[0];

    try {
      await resend.emails.send({
        from: 'the wealth bean <onboarding@thewealthbean.com>',
        to: email,
        subject: 'Welcome to the wealth bean Early Access 🚀',
        html: `
        <div style="font-family: Arial; line-height: 1.6;">
          <h2>🚀 Welcome ${firstName}!</h2>
          <p>You’re officially on the early access list.</p>
          <p><strong>You get 1 month PRO free.</strong></p>
          <p>— The Wealth Bean</p>
        </div>
        `
      });
    } catch (emailError) {
      console.error('Email failed:', emailError);
    }

    res.status(201).json({ success: true, message: 'Joined successfully!' });

  } catch (error) {
    console.error('Server error:', error);
    res.status(500).json({ success: false, message: 'Internal server error.' });
  }
});

// ================================
// ANALYZE ROUTE (FINAL FIXED)
// ================================
app.post('/api/analyze', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) {
      return res.status(400).json({ error: "No file uploaded" });
    }

    console.log("Uploading:", req.file.originalname);

    const form = new FormData();
    form.append(
      'file',
      fs.createReadStream(req.file.path),
      req.file.originalname
    );

    const response = await axios.post(
      'https://twb-python-engine.onrender.com/analyse/single',
      form,
      {
        headers: form.getHeaders(),
        timeout: 60000
      }
    );

    fs.unlinkSync(req.file.path);

    res.json(response.data);

  } catch (err) {
    console.error("FULL ERROR:", err.response?.data || err.message);

    if (req.file && fs.existsSync(req.file.path)) {
      fs.unlinkSync(req.file.path);
    }

    res.status(500).json({
      error: "Analysis failed",
      details: err.response?.data || err.message
    });
  }
});

// ================================
// START SERVER
// ================================
app.listen(PORT, () => {
  console.log(`Backend server running on http://localhost:${PORT}`);
});
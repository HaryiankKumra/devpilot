/**
 * Route table.
 *
 * Auth pages sit outside `AppLayout` because they must render before a user
 * exists. Everything else sits behind `ProtectedRoute`, which redirects to the
 * login page and remembers where the user was heading.
 */

import { AppLayout } from '@/components/AppLayout';
import { ProtectedRoute } from '@/components/ProtectedRoute';
import { DashboardPage } from '@/pages/DashboardPage';
import { LandingPage } from '@/pages/LandingPage';
import { LoginPage } from '@/pages/LoginPage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { PullRequestDetailPage } from '@/pages/PullRequestDetailPage';
import { RegisterPage } from '@/pages/RegisterPage';
import { RepositoriesPage } from '@/pages/RepositoriesPage';
import { RepositoryDetailPage } from '@/pages/RepositoryDetailPage';
import { ReviewDetailPage } from '@/pages/ReviewDetailPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { createBrowserRouter } from 'react-router-dom';

export const router = createBrowserRouter([
  // Public: the front page explains the product to someone with no account.
  { path: '/', element: <LandingPage /> },
  { path: '/login', element: <LoginPage /> },
  { path: '/register', element: <RegisterPage /> },
  {
    path: '/',
    element: (
      <ProtectedRoute>
        <AppLayout />
      </ProtectedRoute>
    ),
    children: [
      { path: 'dashboard', element: <DashboardPage /> },
      { path: 'repositories', element: <RepositoriesPage /> },
      { path: 'repositories/:repositoryId', element: <RepositoryDetailPage /> },
      { path: 'pull-requests/:pullRequestId', element: <PullRequestDetailPage /> },
      { path: 'reviews/:reviewId', element: <ReviewDetailPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);
